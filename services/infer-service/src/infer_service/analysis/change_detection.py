"""端到端变化检测编排（设计 §3.3.2，FR-1，P-016 实现）。

与分割同管线，输入双时相影像；loader 读双图后先经 alignment 配准校验
（§3.2.5，超阈值返 ALIGNMENT_FAILED，评审 D-03 / P-018a）；
engine.detect_change 输出变化概率图 → 按 threshold 二值化
（FR-1.5 同时返回概率图供前端动态调阈）→ 后处理 → 变化蒙版 + 概率图返回。
"""
from __future__ import annotations

import time

import numpy as np
import structlog

from cv_common.errors import GeoExtentMismatchError, InputTooLargeError
from cv_common.geo import pixel_area_m2
from cv_common.imaging import encode_png_b64
from cv_common.schemas.change import (
    ChangeDetectRequest,
    ChangeMaskResponse,
    ChangeStatistics,
)

from infer_service.config import settings
from infer_service.core import concurrency
from infer_service.engines import registry
from infer_service.pipeline import alignment, loader, postprocess, preprocess, tiler

logger = structlog.get_logger(__name__)


def run_change_detection(req: ChangeDetectRequest, provider_hint: str | None = None) -> ChangeMaskResponse:
    """端到端变化检测全管线编排（流程见模块 docstring）。"""
    started = time.perf_counter()

    # ① 读双图（geo_transform：GeoTIFF 内嵌优先，PNG 由 geo_extent 推导）
    before, geo_transform = loader.load_image(req.before_url, req.geo_extent)
    after, _ = loader.load_image(req.after_url, req.geo_extent)
    if before.shape[:2] != after.shape[:2]:
        raise GeoExtentMismatchError(
            f"双期影像尺寸不一致：{before.shape[:2]} vs {after.shape[:2]}（须同源同层级瓦片）"
        )

    # ② 配准校验（FR-1.2 告警路径，§3.2.5）：推理前执行；auto_align 透传记录不改流程
    estimated_shift = alignment.check_alignment(before, after, settings.alignment.max_shift_px)

    engine = registry.get_engine(provider_hint)
    info = engine.info_for("change_detection", req.model_name, req.model_version)  # type: ignore[attr-defined]
    actual_provider = getattr(engine, "actual_provider", info.provider)

    # 输入规模防御（P-025）：local-cpu 模式超限拒绝（INPUT_TOO_LARGE，需求 7.7 R-04）
    if actual_provider == "local-cpu" and max(before.shape[:2]) > settings.limits.local_cpu_max_input:
        raise InputTooLargeError(
            f"输入影像 {before.shape[1]}x{before.shape[0]} 超本地 CPU 模式可处理上限 "
            f"{settings.limits.local_cpu_max_input}px（请改用远程/GPU 提供方或缩小范围）"
        )

    # ③④ 流式切分 → 双时相推理 → 均值融合（推理信号量限流，设计 §5 / P-018）
    height, width = before.shape[:2]
    canvas, count = tiler.alloc_canvas(height, width, 1)
    tile_count = 0
    semaphore = concurrency.get_inference_semaphore(actual_provider)
    tiles_before = tiler.split_tiles(before, settings.tiling.tile_size, settings.tiling.overlap)
    tiles_after = tiler.split_tiles(after, settings.tiling.tile_size, settings.tiling.overlap)
    with semaphore:
        for (tb, offset), (ta, _) in zip(tiles_before, tiles_after, strict=True):
            prob = engine.detect_change(
                [preprocess.preprocess_tile(tb)],
                [preprocess.preprocess_tile(ta)],
                req.model_name,
                req.model_version,
            )[0]
            tiler.fuse_tile(canvas, prob, offset, count)
            tile_count += 1
    probmap = tiler.finalize_canvas(canvas, count)[0]  # [H,W] 变化概率

    # ⑤ 阈值二值化 + 后处理（FR-1.5/1.6）
    mask = postprocess.postprocess_mask((probmap >= req.threshold).astype(np.uint8), req.min_area)

    # ⑥ 统计（FR-1.8）：变化区域数量、总面积（px + m² 逐行积分）
    statistics = _change_statistics(mask, geo_transform)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "change_detection_done",
        tiles=tile_count,
        image_size=f"{width}x{height}",
        estimated_shift_px=round(estimated_shift, 2),
        actual_provider=actual_provider,
        elapsed_ms=elapsed_ms,
    )
    return ChangeMaskResponse(
        mask_png_b64=encode_png_b64((mask * 255).astype(np.uint8)),
        probmap_png_b64=encode_png_b64((np.clip(probmap, 0, 1) * 255).astype(np.uint8)),
        statistics=statistics,
        estimated_shift_px=round(estimated_shift, 2),
        auto_aligned=False,  # 自动配准列 M6（评审 D-03）
        geo_transform=geo_transform,
        model_info=info,
        actual_provider=actual_provider,
        elapsed_ms=elapsed_ms,
    )


def _change_statistics(mask: np.ndarray, geo_transform: list[float] | None) -> ChangeStatistics:
    """变化统计（FR-1.8）：区域数量 + 总面积（px；有 geo_transform 时逐行积分 m²）。"""
    import cv2

    total_area_px = int(mask.sum())
    change_count = 0
    if total_area_px > 0:
        num, _ = cv2.connectedComponents(mask, connectivity=8)
        change_count = num - 1
    total_area_m2: float | None = None
    if geo_transform is not None and total_area_px > 0:
        rows = np.nonzero(mask.any(axis=1))[0]
        total_area_m2 = float(
            sum(pixel_area_m2(geo_transform, int(r), 0) * int(mask[r].sum()) for r in rows)
        )
    return ChangeStatistics(
        change_count=change_count,
        total_area_px=total_area_px,
        total_area_m2=total_area_m2,
    )
