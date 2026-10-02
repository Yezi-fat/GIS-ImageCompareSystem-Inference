"""P-020 验收：矢量化顶点/面积/bbox 精度、Geod 面积独立验算一致、
单独调用与管线内调用结果一致（懒调用解耦）、/compute/vectorize 全链路。
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from cv_common.geo import extent_to_geo_transform, geodesic_area
from cv_common.imaging import encode_png_b64

from compute_service.analysis.vectorizer import vectorize

GEO_TRANSFORM = extent_to_geo_transform([104.0, 30.0, 105.0, 31.0], 100, 100)


def _square_mask(x0=20, y0=20, size=40):
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[y0 : y0 + size, x0 : x0 + size] = 1
    return mask


class TestVectorize:
    def test_square_polygon(self):
        """40×40 方块 → 单多边形，顶点/面积/bbox 精度（像素坐标）。

        口径：area_px 为多边形面积（Shapely/轮廓 Green 定理），40×40 像素块的
        边界坐标为 20..59 → 面积 39×39=1521（设计 §3.3.4 area_px=poly_px.area）。
        """
        regions = vectorize(_square_mask(), min_area=100)
        assert len(regions) == 1
        r = regions[0]
        assert r.area_m2 is None  # 无 geo_transform
        assert r.area_px == pytest.approx(39 * 39, rel=0.01)
        assert r.bbox == pytest.approx([20, 20, 59, 59], abs=0.6)
        assert r.centroid == pytest.approx([39.5, 39.5], abs=0.6)
        # 抽稀后矩形应为四顶点
        assert len(r.geojson["coordinates"][0]) <= 6

    def test_min_area_filter(self):
        mask = _square_mask(size=40)
        mask[90:92, 90:92] = 1  # 4px 小斑块
        regions = vectorize(mask, min_area=100)
        assert len(regions) == 1  # 小斑块被过滤

    def test_geo_coordinates(self):
        """像素→经纬度：bbox 落在 geo_extent 内且位置正确（y 轴向下，纬度随 y 递减）。"""
        regions = vectorize(_square_mask(), min_area=100, geo_transform=GEO_TRANSFORM)
        r = regions[0]
        # 像素 [20,59] × [20,59] → 经度 104.2~104.59，纬度 31-0.59=30.41 ~ 31-0.2=30.8
        assert r.bbox == pytest.approx([104.2, 30.41, 104.59, 30.8], abs=0.01)

    def test_geod_area_independent_check(self):
        """area_m2 与 Geod 独立验算一致（约 0.39°×0.39° 在纬度 30.6 附近）。"""
        from shapely.geometry import box

        regions = vectorize(_square_mask(), min_area=100, geo_transform=GEO_TRANSFORM)
        expected = geodesic_area(box(104.2, 30.41, 104.59, 30.8))
        assert regions[0].area_m2 == pytest.approx(expected, rel=0.02)

    def test_lazy_call_consistency(self):
        """懒调用解耦：函数直调与 API 调用结果一致（V1.2 约定）。"""
        from compute_service.main import app

        client = TestClient(app)
        mask = _square_mask()
        direct = vectorize(mask.copy(), min_area=100, geo_transform=GEO_TRANSFORM)
        resp = client.post("/compute/vectorize", json={
            "mask_b64": encode_png_b64(mask * 255),
            "min_area": 100,
            "geo_transform": GEO_TRANSFORM,
        })
        assert resp.status_code == 200, resp.text
        api_regions = resp.json()["regions"]
        assert len(api_regions) == len(direct) == 1
        assert api_regions[0]["area_px"] == pytest.approx(direct[0].area_px)
        assert api_regions[0]["bbox"] == pytest.approx(direct[0].bbox, abs=1e-9)

    def test_multi_regions(self):
        """差异任务新增/减少分开矢量化的基础：多区域各自输出。"""
        mask = _square_mask(20, 20, 20)
        mask[70:90, 70:90] = 1
        regions = vectorize(mask, min_area=100, geo_transform=GEO_TRANSFORM)
        assert len(regions) == 2
