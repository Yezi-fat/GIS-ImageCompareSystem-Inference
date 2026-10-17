"""统一要素目录契约（工作任务十二，V1.3）：GET /infer/elements。

两套模型（SegFormer-RS/Dynamic World 9 类、YOLO/EuroSAT 10 类）识别要素不同，
前端只展示一套要素——以 Dynamic World 9 类为基准建立统一要素目录，
跨模型代号映射由 infer-service L1 配置（ELEMENTS__MAPPING）持有，本接口对外暴露，
供 Java 要素目录（element_catalog）与前端选择器同步数据源。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ModelClassRefSchema(BaseModel):
    """某模型对某统一要素的识别能力（代号 + 名称）。"""

    codes: list[int] = Field(description="模型输出类别代号列表（一对多支持；空 = 该模型不支持此要素）")
    names: list[str] = Field(default_factory=list, description="代号对应的模型类别名称")


class UnifiedElementItem(BaseModel):
    """统一要素条目。"""

    element: str = Field(description="统一要素 ID（业务/前端唯一口径）")
    name: str = Field(description="统一要素显示名")
    models: dict[str, ModelClassRefSchema] = Field(description="模型名 → 代号映射")


class ElementMappingResponse(BaseModel):
    """GET /infer/elements 响应：统一要素目录 + 跨模型代号映射。"""

    elements: list[UnifiedElementItem]
