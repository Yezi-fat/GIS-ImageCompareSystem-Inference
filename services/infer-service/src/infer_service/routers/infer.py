"""/infer/* 路由（设计 §2.3）。

M2：/infer/segmentation 全管线（P-012）；
M3：/infer/change-detection 全管线（P-016，含配准校验 P-018a）。
provider 提示头 X-Inference-Provider（J-01）透传引擎注册表。
"""
from __future__ import annotations

from fastapi import APIRouter, Header

from cv_common.schemas.change import ChangeDetectRequest, ChangeMaskResponse
from cv_common.schemas.segmentation import SegmentationRequest, SegmentationResponse

from infer_service.analysis.change_detection import run_change_detection
from infer_service.analysis.segmentation import run_segmentation

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
