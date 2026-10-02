"""要素识别（语义分割）接口契约：POST /infer/segmentation（设计 §4.1，FR-6）。

M1 骨架里程碑：schema 字段完整定义（契约即本文件），实现留空。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from cv_common.schemas.health import ModelInfo


class SegmentationRequest(BaseModel):
    """要素识别请求（设计 §4.1）。

    业务策略参数（类别映射/配色/min_area）由 Java 运行配置持有、逐请求下发（FR-10）。
    """

    image_url: str = Field(description="Java 签发的对象存储内网预签名 URL（评审 P-03）")
    model_name: str | None = Field(default=None, description="缺省用默认版本（FR-3.5 切换链路，评审 P-02）")
    model_version: str | None = Field(default=None, description="缺省用默认版本")
    elements: list[str] = Field(min_length=1, description="要素类别 ID 列表（多选，FR-6.1）")
    class_mapping: dict[str, int] = Field(description="要素类别 ID → 模型输出类别 ID（Java 自 element_catalog 下发）")
    colors: dict[str, str] = Field(description="要素类别 ID → 叠加颜色（#RRGGBB，Java 自 element_catalog 下发）")
    min_area: int = Field(default=100, ge=0, description="最小斑块面积（像素，FR-1.6/6.5）")
    geo_extent: list[float] = Field(
        min_length=4, max_length=4,
        description="[minx,miny,maxx,maxy]，CGCS2000 经纬度，必填（评审 D-01）："
                    "无内嵌 transform 的影像据此推导 geo_transform，GeoTIFF 场景用于交叉校验",
    )


class ClassStatistics(BaseModel):
    """单类要素统计（FR-6.5，双单位口径：area_px + area_m2，需求 V1.6）。"""

    area_px: int = Field(ge=0, description="覆盖面积（像素数）")
    area_m2: float | None = Field(default=None, description="地理面积（平方米，CGCS2000 椭球大地测量）")
    ratio: float = Field(ge=0.0, le=1.0, description="占影像比例")
    patch_count: int = Field(ge=0, description="斑块数量")


class SegmentationResponse(BaseModel):
    """要素识别响应（设计 §4.1）。"""

    combined_mask_png_b64: str = Field(description="按类别着色的合成蒙版 PNG（RGBA，非覆盖区域透明，FR-6.3）base64")
    per_class_masks: dict[str, str] = Field(description="各类别独立二值蒙版 base64（前端按类别开关图层，FR-6.3）")
    statistics: dict[str, ClassStatistics] = Field(description="按要素类别键控的统计")
    geo_transform: list[float] | None = Field(
        default=None, min_length=6, max_length=6,
        description="GDAL 六参数仿射变换 [x0,px_w,0,y0,0,-px_h]；Java 透传给 compute 侧 diff/vectorize",
    )
    model_info: ModelInfo = Field(description="实际使用的模型名/版本/provider/类别表（FR-3.3）")
    actual_provider: str = Field(description="实际执行提供方（与请求不一致时如实标记，降级场景）")
    elapsed_ms: int = Field(ge=0, description="端到端耗时（毫秒）")
