"""双期要素差异计算接口契约：POST /compute/diff（设计 §4.3，FR-7.2~7.5）。

M1 骨架里程碑：schema 字段完整定义，实现留空。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DiffColors(BaseModel):
    """差异分色语义（FR-7.3，可配置；Java 自 capabilities defaults.diff_colors 下发）。"""

    added: str = Field(default="#00FF00", description="新增（仅新图有）")
    removed: str = Field(default="#FF0000", description="减少（仅旧图有）")


class DiffRequest(BaseModel):
    """差异计算请求（设计 §4.3）：输入为两期二值蒙版 base64 PNG，无需重新推理。

    Java 编排：同一要素两期分割 mask 在同一请求中给出，逐要素类别调用。
    """

    before_mask_b64: str = Field(description="旧期要素二值蒙版 PNG base64")
    after_mask_b64: str = Field(description="新期要素二值蒙版 PNG base64")
    element: str = Field(description="要素类别 ID（单类别逐次调用）")
    min_area: int = Field(default=100, ge=0, description="最小差异面积阈值（像素，FR-7.4）")
    colors: DiffColors = Field(default_factory=DiffColors)
    geo_transform: list[float] | None = Field(
        default=None, min_length=6, max_length=6,
        description="GDAL 六参数仿射变换（来自 infer 响应，Java 透传）；有值时输出 area_m2",
    )


class DiffStatistics(BaseModel):
    """差异统计（FR-7.5，双单位口径 px + m²）。"""

    added_px: int = Field(ge=0, description="新增面积（像素）")
    removed_px: int = Field(ge=0, description="减少面积（像素）")
    net_change_px: int = Field(description="净变化量（像素，after-before）")
    change_rate: float = Field(description="变化率 (after-before)/before")
    added_m2: float | None = Field(default=None, description="新增地理面积（平方米）")
    removed_m2: float | None = Field(default=None, description="减少地理面积（平方米）")


class DiffResponse(BaseModel):
    """差异计算响应（设计 §4.3）。"""

    diff_mask_png_b64: str = Field(description="差异蒙版 PNG base64：added/removed 分色，不变区域透明（FR-7.3）")
    statistics: DiffStatistics
