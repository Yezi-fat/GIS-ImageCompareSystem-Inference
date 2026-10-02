"""CGCS2000 坐标工具（设计 §2.2，V1.1 坐标系约束的代码落点）。

系统坐标统一为 CGCS2000 经纬度投影（非 Web Mercator）；
经纬度下的面积/中心点等量算必须采用椭球大地测量方法（需求约束 #5）。

实现里程碑：P-009（geodesic_area 落地；纯公式函数 M1 已实现）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pyproj import Geod

if TYPE_CHECKING:
    import shapely.geometry

# CGCS2000 椭球参数（长半轴 a、扁率 rf）
GEOD = Geod(a=6378137.0, rf=298.257222101)


def geodesic_area(poly: "shapely.geometry.Polygon") -> float:
    """椭球面面积（平方米，CGCS2000 椭球大地测量；外环减内环）。

    FR-6.5/7.5 地理面积换算的唯一入口。输入为经纬度（度）坐标的 Shapely Polygon。
    """
    area, _ = GEOD.polygon_area_perimeter(
        [c[0] for c in poly.exterior.coords],
        [c[1] for c in poly.exterior.coords],
    )
    total = abs(area)
    for interior in poly.interiors:
        hole_area, _ = GEOD.polygon_area_perimeter(
            [c[0] for c in interior.coords],
            [c[1] for c in interior.coords],
        )
        total -= abs(hole_area)
    return total


def pixel_area_m2(geo_transform: list[float], row: int, col: int) -> float:
    """单个像素在椭球面上的面积（平方米）——按像素中心纬度的大地测量面积。

    经纬度等间隔网格下，像素面积仅随纬度变化；统计口径（§3.3.1 ⑧）按行积分时
    逐行调用本函数即可。geo_transform 为 GDAL 六参数 [x0,px_w,0,y0,0,-px_h]。
    """
    x0, px_w, _, y0, _, px_h = geo_transform
    west = x0 + col * px_w
    east = west + px_w
    north = y0 + row * px_h
    south = north + px_h
    lons = [west, east, east, west]
    lats = [south, south, north, north]
    area, _ = GEOD.polygon_area_perimeter(lons, lats)
    return abs(area)


def tile_span(z: int, span_base: float = 360.0) -> float:
    """span(z) = 360 / 2^z（度），经纬度瓦片方案（非 Web Mercator）。"""
    return span_base / (2**z)


def tile_range_to_extent(
    z: int,
    x_min: int,
    x_max: int,
    y_min: int,
    y_max: int,
    origin: tuple[float, float] = (-180.0, 90.0),
) -> list[float]:
    """瓦片范围 → [minx, miny, maxx, maxy]（度，线性公式）。

    origin 为瓦片矩阵原点（默认左上角 (-180, 90)，FR-10.6 配置化，Java capabilities 同源下发）。
    x/y 范围均含端点（[x_min, x_max] 共 x_max-x_min+1 列瓦片）。
    """
    span = tile_span(z)
    ox, oy = origin
    minx = ox + x_min * span
    maxx = ox + (x_max + 1) * span
    maxy = oy - y_min * span
    miny = oy - (y_max + 1) * span
    return [minx, miny, maxx, maxy]


def extent_to_geo_transform(geo_extent: list[float], width: int, height: int) -> list[float]:
    """geo_extent + 图像宽高 → GDAL 六参数仿射变换（线性推导，评审 D-01）。

    [minx, (maxx-minx)/width, 0, maxy, 0, -(maxy-miny)/height]
    loader 解码无内嵌 transform 时调用；与 tile_range_to_extent 同源的线性公式。
    """
    minx, miny, maxx, maxy = geo_extent
    px_w = (maxx - minx) / width
    px_h = (maxy - miny) / height
    return [minx, px_w, 0.0, maxy, 0.0, -px_h]
