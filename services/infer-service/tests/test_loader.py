"""P-010 验收：loader 三格式解码、CRS 校验、URL 失败语义。

respx 打桩预签名 URL（全局要求 #5：不依赖真实对象存储）。
"""
import io

import cv2
import numpy as np
import pytest
import respx
import httpx

from cv_common.errors import CvError, GeoExtentMismatchError, ImageDecodeFailedError

from infer_service.pipeline import loader

URL = "http://oss-internal.test/bucket/img?sign=abc"
GEO_EXTENT = [104.0, 30.0, 105.0, 31.0]


def _png_bytes(img: np.ndarray) -> bytes:
    return cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()


def _jpeg_bytes(img: np.ndarray) -> bytes:
    return cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()


def _geotiff_bytes(crs_epsg: int, width=100, height=50, extent=GEO_EXTENT) -> bytes:
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_bounds

    transform = from_bounds(*extent, width, height)
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", width=width, height=height, count=3, dtype="uint8",
            crs=f"EPSG:{crs_epsg}", transform=transform,
        ) as ds:
            ds.write(np.full((3, height, width), 128, dtype=np.uint8))
        return memfile.read()


@respx.mock
def test_load_png_derives_transform():
    """PNG 无内嵌 CRS → geo_transform 由 geo_extent 线性推导（评审 D-01）。"""
    img = np.zeros((50, 100, 3), dtype=np.uint8)
    respx.get(URL).mock(return_value=httpx.Response(200, content=_png_bytes(img)))

    image, gt = loader.load_image(URL, GEO_EXTENT)

    assert image.shape == (50, 100, 3) and image.dtype == np.uint8
    assert gt == [104.0, 0.01, 0.0, 31.0, 0.0, -0.02]


@respx.mock
def test_load_jpeg():
    img = np.full((40, 60, 3), 200, dtype=np.uint8)
    respx.get(URL).mock(return_value=httpx.Response(200, content=_jpeg_bytes(img)))
    image, _ = loader.load_image(URL, GEO_EXTENT)
    assert image.shape == (40, 60, 3)


@respx.mock
def test_load_geotiff_cgcs2000_ok():
    """GeoTIFF（EPSG:4490）内嵌 transform 与 geo_extent 一致 → 交叉校验通过。"""
    respx.get(URL).mock(return_value=httpx.Response(200, content=_geotiff_bytes(4490)))
    image, gt = loader.load_image(URL, GEO_EXTENT)
    assert image.shape == (50, 100, 3)
    assert gt[0] == pytest.approx(104.0) and gt[3] == pytest.approx(31.0)


@respx.mock
def test_load_geotiff_projected_crs_rejected():
    """投影坐标系（EPSG:3857）GeoTIFF → INVALID_INPUT。"""
    respx.get(URL).mock(return_value=httpx.Response(200, content=_geotiff_bytes(3857)))
    with pytest.raises(CvError) as exc:
        loader.load_image(URL, GEO_EXTENT)
    assert exc.value.code == "INVALID_INPUT"


@respx.mock
def test_load_geotiff_extent_mismatch():
    """GeoTIFF 内嵌范围与请求 geo_extent 不一致 → GEO_EXTENT_MISMATCH。"""
    respx.get(URL).mock(return_value=httpx.Response(200, content=_geotiff_bytes(4326)))
    with pytest.raises(GeoExtentMismatchError):
        loader.load_image(URL, [110.0, 30.0, 111.0, 31.0])


@respx.mock
def test_url_expired_403():
    """预签名 URL 过期（403）→ IMAGE_DECODE_FAILED，message 携带 HTTP 状态供 Java 重签判定（J-02）。"""
    respx.get(URL).mock(return_value=httpx.Response(403, text="expired"))
    with pytest.raises(ImageDecodeFailedError) as exc:
        loader.load_image(URL, GEO_EXTENT)
    assert "403" in exc.value.message


@respx.mock
def test_url_network_failure():
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ImageDecodeFailedError):
        loader.load_image(URL, GEO_EXTENT)
