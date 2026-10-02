"""三服务健康检查响应模型（设计 §3.5，FR-5.5/5.7）。

Java 三级提供方解析与 capabilities 聚合依赖 infer-service 的 /health 字段；
nlp-service 的 nlu.circuit 供熔断监控告警（FR-9.7）。

M1 骨架里程碑：字段完整定义，M1 阶段各服务 /health 返回占位置（P-008）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    """模型信息（FR-3.3：任务记录 provider + 模型名 + 版本）。

    engines 的 info() 返回本模型；健康检查与分析响应复用。
    """

    name: str = Field(description="模型名，如 landcover-seg")
    version: str = Field(description="模型版本，如 v2.0")
    provider: str | None = Field(default=None, description="remote / local-gpu / local-cpu")
    class_ids: list[int] | None = Field(default=None, description="分割模型支持的输出类别 ID 列表")


class ModelHealth(BaseModel):
    """单模型就绪状态（FR-5.5）。"""

    loaded: bool = Field(description="会话是否已加载常驻")
    version: str | None = Field(default=None)
    provider: str | None = Field(default=None)
    class_ids: list[int] | None = Field(default=None, description="仅分割模型上报")


class InferModelsHealth(BaseModel):
    """infer-service 两类模型就绪状态。"""

    segmentation: ModelHealth
    change_detect: ModelHealth


class InferHealthResponse(BaseModel):
    """infer-service /health（设计 §3.5）——Java 三级解析（FR-5.6/5.7）数据源。"""

    status: str = Field(description="ok / degraded / error")
    gpu_available: bool = Field(description="CUDA 可用性探测（onnxruntime providers + 试建小会话）")
    gpu_usable: bool = Field(description="GPU 实际可用（显存满足模型要求）")
    vram_mb: int | None = Field(default=None, description="显存容量（pynvml，GPU 版镜像）")
    cpu_cores: int = Field(ge=1)
    models: InferModelsHealth
    remote_configured: bool = Field(description="是否配置了远程推理端点（未配置属正常形态，FR-5.6）")


class ComputeHealthResponse(BaseModel):
    """compute-service /health（轻量，无模型）。"""

    status: str
    cpu_cores: int = Field(ge=1)


CircuitState = Literal["closed", "half_open", "open"]


class NluHealth(BaseModel):
    """NLU 能力状态（FR-9.7：熔断状态上报供 Java 监控告警）。"""

    provider: str = Field(description="llm / rule-based（未配置 LLM 时常驻规则解析）")
    available: bool
    circuit: CircuitState = Field(description="进程内熔断器状态（FR-9.7）")


class GeocoderHealth(BaseModel):
    """地理编码能力状态（FR-9.2）。"""

    provider: str = Field(description="amap / baidu / nominatim")
    available: bool


class NlpHealthResponse(BaseModel):
    """nlp-service /health（设计 §3.5）。"""

    status: str
    nlu: NluHealth
    geocoder: GeocoderHealth
