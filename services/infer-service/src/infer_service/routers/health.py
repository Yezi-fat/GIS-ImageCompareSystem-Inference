"""/health 路由（设计 §3.5，P-013 真实化）：Java 三级提供方解析（FR-5.6/5.7）
与 capabilities 聚合的数据源。

上报：CUDA 可用性（试建小会话）、显存（pynvml）、CPU 核数、
两类模型就绪状态（ModelStore 会话缓存）、远程端点配置与否。
无 GPU 环境 gpu_available=false 且不影响启动。
"""
from __future__ import annotations

from fastapi import APIRouter

from cv_common.schemas.health import (
    InferHealthResponse,
    InferModelsHealth,
    ModelHealth,
)

from infer_service.config import settings
from infer_service.core import capability
from infer_service.engines import registry

router = APIRouter()


@router.get("/health", response_model=InferHealthResponse)
def health() -> InferHealthResponse:
    """健康与能力上报（FR-5.5/5.7）。"""
    loaded = registry.model_store().loaded_models()
    return InferHealthResponse(
        status="ok",
        gpu_available=capability.gpu_available(),
        gpu_usable=capability.gpu_usable(),
        vram_mb=capability.vram_mb(),
        cpu_cores=capability.cpu_cores(),
        models=InferModelsHealth(
            segmentation=_model_health(loaded, "segmentation"),
            change_detect=_model_health(loaded, "change_detection"),
        ),
        remote_configured=bool(settings.remote.endpoint),
    )


def _model_health(loaded: dict, model_type: str) -> ModelHealth:
    """从会话缓存键 (model_type, name, version, provider) 汇总模型就绪状态。"""
    entries = [k for k in loaded if k[0] == model_type]
    class_ids = None
    if model_type == "segmentation":
        # 有效类别表：显式配置优先，缺省时从 ONNX names 自动推导（Q2-P2）
        class_ids = registry.model_store().effective_class_ids()
    if not entries:
        return ModelHealth(loaded=False, version=None, provider=None, class_ids=class_ids)
    _, _, version, provider = entries[-1]
    return ModelHealth(loaded=True, version=version, provider=provider, class_ids=class_ids)
