"""轮廓矢量化（设计 §3.3.4，FR-1.7/6.4/7.6，P-020 实现）。

findContours → 面积过滤 → Shapely 抽稀 → 像素转经纬度（geo_transform）→
[{geojson, area_px, area_m2(Geod), centroid, bbox}]；
bbox 为 Java region 表冗余标量列数据源（需求 14.5 无 PostGIS 方案）。
差异任务新增/减少分开矢量化（FR-7.6），region_type 由 Java 按图层类型映射。

懒调用约定（V1.2）：与任务管线解耦，单独调用与管线内调用结果一致（P-020 验收）。
"""
from __future__ import annotations

import cv2
import numpy as np

from cv_common.geo import geodesic_area
from cv_common.schemas.vectorize import RegionItem

# Shapely 抽稀容差（像素，设计 §3.3.4）
_SIMPLIFY_TOLERANCE = 1.0


def vectorize(
    mask: np.ndarray,
    min_area: float,
    geo_transform: list[float] | None = None,
) -> list[RegionItem]:
    """二值蒙版 → 区域多边形列表。

    geo_transform 缺省时输出像素坐标且 area_m2=None；
    有值时输出 CGCS2000 经纬度坐标，area_m2 经 cv_common.geo.geodesic_area 椭球计算。
    """
    from shapely.geometry import Polygon, mapping

    binary = (mask > 0).astype(np.uint8)
    if binary.ndim == 3:  # 容忍传入三通道蒙版
        binary = binary[..., 0]
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    regions: list[RegionItem] = []
    for contour in contours:
        area_px = float(cv2.contourArea(contour))
        if area_px < min_area:
            continue
        points = contour[:, 0, :].astype(np.float64)
        if len(points) < 3:
            continue
        poly = Polygon(points)
        if not poly.is_valid:
            poly = poly.buffer(0)  # 自相交修复
        if poly.is_empty:
            continue
        poly = poly.simplify(_SIMPLIFY_TOLERANCE, preserve_topology=True)

        if geo_transform is not None:
            poly = _pixel_to_geo(poly, geo_transform)
            area_m2: float | None = geodesic_area(poly)
        else:
            area_m2 = None
        centroid = poly.centroid
        regions.append(
            RegionItem(
                geojson=mapping(poly),
                area_px=area_px,
                area_m2=area_m2,
                centroid=[centroid.x, centroid.y],
                bbox=list(poly.bounds),
            )
        )
    return regions


def _pixel_to_geo(poly, geo_transform: list[float]):
    """像素坐标 → CGCS2000 经纬度（GDAL 六参数仿射：x_geo = x0 + px*px_w，y_geo = y0 + py*px_h）。"""
    from shapely import transform

    x0, px_w, _, y0, _, px_h = geo_transform

    def _affine(coords: np.ndarray) -> np.ndarray:
        coords = coords.copy()
        coords[..., 0] = x0 + coords[..., 0] * px_w
        coords[..., 1] = y0 + coords[..., 1] * px_h
        return coords

    return transform(poly, _affine)
