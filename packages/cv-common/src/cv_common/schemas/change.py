"""端到端变化检测接口契约：POST /infer/change-detection（设计 §4.2，FR-1）。

M1 骨架里程碑：schema 字段完整定义，实现留空。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from cv_common.schemas.health import ModelInfo


class ChangeDetectRequest(BaseModel):
    """端到端变化检测请求（设计 §4.2）。"""

    before_url: str = Field(description="旧期影像：Java 签发的内网预签名 URL")
    after_url: str = Field(description="新期影像：Java 签发的内网预签名 URL")
    geo_extent: list[float] = Field(
        min_length=4, max_length=4,
        description="[minx,miny,maxx,maxy]，CGCS2000 经纬度，必填（评审 D-01），语义同 §4.1",
    )
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="变化概率二值化阈值（FR-1.5）")
    min_area: int = Field(default=100, ge=0, description="最小变化区域面积（像素，FR-1.6）")
    auto_align: bool = Field(
        default=False,
        description="自动配准开关（FR-1.2）：本期仅透传记录、不改流程；"
                    "偏差校验恒执行，超阈值返回 ALIGNMENT_FAILED（§3.2.5，评审 D-03）",
    )
    model_name: str | None = Field(default=None, description="缺省用默认版本（FR-3.5）")
    model_version: str | None = Field(default=None)


class ChangeStatistics(BaseModel):
    """变化统计（FR-1.8）。"""

    change_count: int = Field(ge=0, description="变化区域数量")
    total_area_px: int | None = Field(default=None, ge=0, description="变化总面积（像素）")
    total_area_m2: float | None = Field(default=None, description="变化总面积（平方米，CGCS2000 椭球）")


class ChangeMaskResponse(BaseModel):
    """端到端变化检测响应（设计 §4.2）。"""

    mask_png_b64: str = Field(description="变化蒙版 PNG base64（与输入同尺寸，FR-1.4）")
    probmap_png_b64: str = Field(description="变化概率图 PNG base64（FR-1.5，前端按阈值动态调整）")
    statistics: ChangeStatistics
    estimated_shift_px: float = Field(description="配准校验实测整体平移量（像素，§3.2.5，评审 D-03）")
    auto_aligned: bool = Field(default=False, description="本期恒为 false——自动配准列 M6（评审 D-03）")
    geo_transform: list[float] | None = Field(
        default=None, min_length=6, max_length=6,
        description="GDAL 六参数仿射变换；Java 透传给 compute 侧 vectorize",
    )
    model_info: ModelInfo = Field(description="实际使用的模型名/版本/provider（FR-3.3）")
    actual_provider: str = Field(description="实际执行提供方（降级时如实标记）")
    elapsed_ms: int = Field(ge=0)
