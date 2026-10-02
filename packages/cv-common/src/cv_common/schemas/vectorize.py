"""轮廓矢量化接口契约：POST /compute/vectorize（设计 §4.4，FR-1.7/6.4/7.6）。

支持懒调用（V1.2 约定）：与任务管线解耦，任务执行期可跳过、
导出时由 Java result-service 现调；无状态设计天然支持。

M1 骨架里程碑：schema 字段完整定义，实现留空。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VectorizeRequest(BaseModel):
    """矢量化请求（设计 §4.4）。"""

    mask_b64: str = Field(description="二值蒙版 PNG base64")
    min_area: float = Field(default=100.0, ge=0.0, description="最小区域面积过滤阈值（像素）")
    geo_transform: list[float] | None = Field(
        default=None, min_length=6, max_length=6,
        description="GDAL 六参数仿射变换；有值时输出经纬度坐标与 area_m2，缺省时输出像素坐标",
    )


class RegionItem(BaseModel):
    """单个矢量化区域（设计 §3.3.4；bbox 为 Java region 表冗余标量列数据源，需求 14.5）。"""

    geojson: dict[str, Any] = Field(description="GeoJSON 几何（CGCS2000 经纬度；无 geo_transform 时为像素坐标）")
    area_px: float = Field(ge=0.0, description="面积（像素）")
    area_m2: float | None = Field(default=None, description="面积（平方米，pyproj Geod 椭球大地测量）")
    centroid: list[float] = Field(min_length=2, max_length=2, description="中心点 [x, y]")
    bbox: list[float] = Field(min_length=4, max_length=4, description="[minx, miny, maxx, maxy]")


class VectorizeResponse(BaseModel):
    """矢量化响应（设计 §4.4）。差异任务新增/减少分开调用、分别矢量化（FR-7.6）。"""

    regions: list[RegionItem]
