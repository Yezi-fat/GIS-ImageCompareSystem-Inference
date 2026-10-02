"""自然语言任务解析接口契约：POST /nlp/parse（设计 §4.5，需求 7.4，FR-9）。

契约显式化（评审 D-02）：请求含 element_catalog（必填，Java 自 config-service
下发）；响应显式列 uncertain_fields（可空，FR-9.4/9.8）。
要素别名/修饰语词表由 nlp-service 内置维护，不经 Java 下发。

M1 骨架里程碑：schema 字段完整定义，实现留空。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 意图枚举（FR-9.1）
IntentType = Literal["feature_extraction", "feature_comparison", "temporal_analysis"]
# NLU 提供方（FR-9.6/9.7：LLM 故障逐请求降级规则解析并如实标记）
NluProvider = Literal["llm", "rule-based"]


class ElementCatalogItem(BaseModel):
    """要素目录条目（Java 自 config-service element_catalog 下发，J-06/D-02）。"""

    id: str = Field(description="要素类别 ID，如 forest")
    name: str = Field(description="显示名，如 森林")
    model_class_id: int | None = Field(default=None, description="对应分割模型输出类别 ID")
    enabled: bool = Field(default=True, description="启用状态")


class NlParseRequest(BaseModel):
    """自然语言解析请求（设计 §4.5）。"""

    text: str = Field(min_length=1, description="用户自然语言输入，如“识别四川成都金牛区xx街道的建筑群”")
    element_catalog: list[ElementCatalogItem] = Field(
        min_length=1, description="当前可识别要素类别目录（必填，评审 D-02/J-06）",
    )


class LocationCandidate(BaseModel):
    """地理编码候选（歧义地名多候选交由用户选择，FR-9.2）。"""

    name: str = Field(description="候选地名全称")
    bbox: list[float] = Field(min_length=4, max_length=4, description="[minx,miny,maxx,maxy]，CGCS2000 经纬度")
    confidence: float = Field(ge=0.0, le=1.0)


class NlLocation(BaseModel):
    """地理范围解析结果（FR-9.2）。"""

    raw: str = Field(description="原始地名文本")
    bbox: list[float] | None = Field(
        default=None, min_length=4, max_length=4,
        description="地理编码结果 bbox；失败时为 null（部分成功降级，FR-9.8）",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    candidates: list[LocationCandidate] = Field(default_factory=list, description="歧义候选列表（FR-9.2）")


class UnsupportedItem(BaseModel):
    """无法映射的要素描述（FR-9.3：明确告知并列出可识别类别）。"""

    raw: str = Field(description="用户输入中的要素表述（含修饰语），如“沿街建筑群”")
    reason: str = Field(description="无法映射原因")


class NlParseResponse(BaseModel):
    """自然语言解析响应（需求 7.4 + 评审 D-02）。

    need_confirm 恒为 true（FR-9.4 强制用户确认）；
    未经确认不产生任何分析任务（验收标准第 9 条）。
    """

    intent: IntentType | None = Field(
        default=None, description="意图类型；缺失且位置亦缺失时整体返回 LOCATION_UNRESOLVED（FR-9.8）",
    )
    location: NlLocation | None = Field(
        default=None, description="地理编码失败时为 null + uncertain_fields 含 location（部分成功降级，FR-9.8）",
    )
    elements: list[str] = Field(default_factory=list, description="映射后的要素类别 ID 列表（FR-9.3）")
    periods: list[str] = Field(default_factory=list, description="期次标签列表（多期意图时）")
    confidence: float = Field(ge=0.0, le=1.0, description="整体解析置信度")
    need_confirm: bool = Field(default=True, description="恒为 true（FR-9.4）")
    unsupported: list[UnsupportedItem] = Field(default_factory=list, description="无法映射的要素表述及原因（FR-9.3）")
    nlu_provider: NluProvider = Field(description="llm / rule-based（降级如实标记，FR-9.7）")
    uncertain_fields: list[str] | None = Field(
        default=None, description="低置信度/失败字段标注（FR-9.4/9.8），无则 null",
    )
