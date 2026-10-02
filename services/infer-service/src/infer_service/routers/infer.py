"""/infer/* 路由（设计 §2.3）。

M2：/infer/segmentation 全管线（P-012）；
M3：/infer/change-detection 全管线（P-016，含配准校验 P-018a）。
provider 提示头 X-Inference-Provider（J-01）透传引擎注册表。
v1.0.0：GET /infer/models 模型发现（bug-2026-09-28 Q2-P1/Q3-P1）。
"""
from __future__ import annotations

from fastapi import APIRouter, Header

from cv_common.schemas.change import ChangeDetectRequest, ChangeMaskResponse
from cv_common.schemas.models import ModelsResponse
from cv_common.schemas.segmentation import SegmentationRequest, SegmentationResponse

from infer_service.analysis.change_detection import run_change_detection
from infer_service.analysis.segmentation import run_segmentation
from infer_service.config import settings
from infer_service.engines import discovery, registry

router = APIRouter()


@router.post("/infer/segmentation", response_model=SegmentationResponse)
def segmentation(
    req: SegmentationRequest,
    x_inference_provider: str | None = Header(default=None),
) -> SegmentationResponse:
    """要素识别（语义分割，FR-6）：loader→tiler→engine→融合→后处理→着色→统计。"""
    return run_segmentation(req, provider_hint=x_inference_provider)


@router.post("/infer/change-detection", response_model=ChangeMaskResponse)
def change_detection(
    req: ChangeDetectRequest,
    x_inference_provider: str | None = Header(default=None),
) -> ChangeMaskResponse:
    """端到端变化检测（FR-1）：配准校验（§3.2.5）→ 双时相推理 → 阈值二值化 → 后处理。"""
    return run_change_detection(req, provider_hint=x_inference_provider)


@router.get("/infer/models", response_model=ModelsResponse)
def list_models() -> ModelsResponse:
    """模型发现（bug-2026-09-28 Q2/Q3）：扫描模型目录，上报双产物齐全性、
    类别表（ONNX 元数据 names）、加载状态与默认版本——供 Java 模型管理页
    展示/对账与 element_catalog 映射校验。"""
    return discovery.scan_models(settings, registry.model_store())
