"""/compute/* 路由（设计 §2.4）：diff、vectorize、health 三端点。

M3 里程碑：diff（P-019）与 vectorize（P-020）全量实现，懒调用解耦
（与任务管线解耦，result-service 导出时现调）。
"""
from __future__ import annotations

import os
import time

import structlog

from fastapi import APIRouter

from cv_common.imaging import decode_b64_png, encode_png_b64
from cv_common.schemas.diff import DiffRequest, DiffResponse
from cv_common.schemas.health import ComputeHealthResponse
from cv_common.schemas.vectorize import VectorizeRequest, VectorizeResponse

from compute_service.analysis.differ import compute_diff
from compute_service.analysis.vectorizer import vectorize

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post("/compute/diff", response_model=DiffResponse)
def diff(req: DiffRequest) -> DiffResponse:
    """双期差异逐像素计算（FR-7.2~7.5），支持懒调用（无状态）。"""
    started = time.perf_counter()
    before = decode_b64_png(req.before_mask_b64)
    after = decode_b64_png(req.after_mask_b64)
    diff_mask, statistics = compute_diff(
        before, after, req.min_area, req.colors, req.geo_transform
    )
    logger.info("diff_done", element=req.element,
                added_px=statistics.added_px, removed_px=statistics.removed_px,
                elapsed_ms=int((time.perf_counter() - started) * 1000))
    return DiffResponse(
        diff_mask_png_b64=encode_png_b64(diff_mask),
        statistics=statistics,
    )


@router.post("/compute/vectorize", response_model=VectorizeResponse)
def vectorize_api(req: VectorizeRequest) -> VectorizeResponse:
    """轮廓矢量化（FR-1.7/6.4/7.6），支持懒调用（V1.2 约定）。"""
    started = time.perf_counter()
    mask = decode_b64_png(req.mask_b64)
    regions = vectorize(mask, req.min_area, req.geo_transform)
    logger.info("vectorize_done", region_count=len(regions),
                elapsed_ms=int((time.perf_counter() - started) * 1000))
    return VectorizeResponse(regions=regions)


@router.get("/health", response_model=ComputeHealthResponse)
def health() -> ComputeHealthResponse:
    """健康检查（轻量，无模型）。"""
    return ComputeHealthResponse(status="ok", cpu_cores=os.cpu_count() or 1)
