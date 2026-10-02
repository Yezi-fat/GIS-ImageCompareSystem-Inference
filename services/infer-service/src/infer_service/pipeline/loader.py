"""影像拉取与解码（设计 §3.2.1，P-010 实现）。

- 按 Java 签发的内网预签名 URL 拉取影像（core/storage_reader，零配置零令牌，评审 P-03）；
- PNG/JPEG 用 OpenCV 解码，TIFF/GeoTIFF 用 rasterio 解码并提取地理参考 transform；
- 影像统一转 RGB uint8 数组；
- 坐标系约定（V1.1）：CGCS2000 经纬度（度）；GeoTIFF 自带 CRS 时校验为地理坐标系
  （EPSG:4490/4326 系列），投影坐标系拒绝并返回 INVALID_INPUT；
- geo_transform 来源（评审 D-01）：GeoTIFF 内嵌 transform 优先（与请求 geo_extent
  交叉校验，偏差超容差 → GeoExtentMismatchError）；无内嵌 transform 时按请求
  geo_extent + 图像宽高经 cv_common.geo.extent_to_geo_transform 线性推导。
"""
from __future__ import annotations

import cv2
import numpy as np

from cv_common.errors import CvError, GeoExtentMismatchError, ImageDecodeFailedError
from cv_common.geo import extent_to_geo_transform

from infer_service.config import settings
from infer_service.core.storage_reader import fetch_bytes

# GeoTIFF 内嵌 transform 与请求 geo_extent 交叉校验的容差（度，约 1 个像素量级起判）
_EXTENT_TOLERANCE_DEG = 1e-3

# 接受的地理坐标系 EPSG 前缀（CGCS2000=4490、WGS84=4326 及同族经纬度框架）
_GEOGRAPHIC_EPSG = {4326, 4490, 4610, 4214, 4269}


def load_image(url: str, geo_extent: list[float]) -> tuple[np.ndarray, list[float]]:
    """拉取并解码影像 → (RGB uint8 数组, geo_transform 六参数仿射)。

    - 拉取失败/URL 过期 → ImageDecodeFailedError（storage_reader 已携带存储端状态）；
    - GeoTIFF：内嵌 transform + CRS 校验（投影坐标系拒绝 INVALID_INPUT），
      并与请求 geo_extent 交叉校验（超容差 → GeoExtentMismatchError）；
    - PNG/JPEG：geo_transform 由 geo_extent 线性推导（评审 D-01）。
    """
    data = fetch_bytes(url, timeout_s=settings.storage_reader.timeout_s)
    if _is_tiff(data):
        image, geo_transform = _decode_geotiff(data)
        h, w = image.shape[:2]
        cross_check_extent(geo_transform, geo_extent, w, h)
    else:
        image = _decode_plain(data)
        geo_transform = extent_to_geo_transform(geo_extent, image.shape[1], image.shape[0])
    return image, geo_transform


def check_crs(crs: object) -> None:
    """GeoTIFF CRS 校验：仅接受地理坐标系（EPSG:4490/4326 系列），其余拒绝 INVALID_INPUT。"""
    if crs is None:
        return
    epsg = crs.to_epsg() if hasattr(crs, "to_epsg") else None
    is_geographic = bool(getattr(crs, "is_geographic", False))
    if epsg in _GEOGRAPHIC_EPSG or (epsg is None and is_geographic):
        return
    raise CvError(
        "INVALID_INPUT",
        f"GeoTIFF 坐标系须为 CGCS2000/WGS84 经纬度（EPSG:4490/4326 系列），"
        f"当前为 {epsg or crs}（投影坐标系请先转为经纬度）",
        400,
    )


def cross_check_extent(geo_transform: list[float], geo_extent: list[float], width: int, height: int) -> None:
    """GeoTIFF 内嵌 transform 与请求 geo_extent 交叉校验，超容差抛 GeoExtentMismatchError。"""
    x0, px_w, _, y0, _, px_h = geo_transform
    actual = [x0, y0 + height * px_h, x0 + width * px_w, y0]
    if any(abs(a - b) > _EXTENT_TOLERANCE_DEG for a, b in zip(actual, geo_extent, strict=True)):
        raise GeoExtentMismatchError(
            f"GeoTIFF 内嵌地理范围 {[round(v, 6) for v in actual]} 与请求 geo_extent "
            f"{geo_extent} 不一致（容差 {_EXTENT_TOLERANCE_DEG}°）"
        )


def _is_tiff(data: bytes) -> bool:
    """TIFF 魔数：II*\\0（小端）或 MM\\0*（大端）。"""
    return data[:4] in (b"II*\x00", b"MM\x00*")


def _decode_plain(data: bytes) -> np.ndarray:
    """PNG/JPEG → RGB uint8（OpenCV 解码为 BGR，统一转 RGB）。"""
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ImageDecodeFailedError("影像解码失败：非支持的 PNG/JPEG/TIFF/GeoTIFF 格式（8.3 白名单）")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _decode_geotiff(data: bytes) -> tuple[np.ndarray, list[float]]:
    """GeoTIFF → (RGB uint8, 内嵌 geo_transform)；CRS 校验（投影坐标系拒绝）。"""
    import rasterio
    from rasterio.io import MemoryFile

    try:
        with MemoryFile(data) as memfile, memfile.open() as ds:
            check_crs(ds.crs)
            bands = ds.read([1, 2, 3]) if ds.count >= 3 else ds.read(1)[np.newaxis, ...]
            image = np.moveaxis(bands, 0, -1)
            if image.shape[2] == 1:
                image = np.repeat(image, 3, axis=2)
            image = image.astype(np.uint8)
            t = ds.transform
            geo_transform = [t.c, t.a, t.b, t.f, t.d, t.e]
    except (CvError, GeoExtentMismatchError):
        raise
    except Exception as e:
        raise ImageDecodeFailedError(f"GeoTIFF 解码失败：{type(e).__name__}: {e}") from e
    return image, geo_transform
