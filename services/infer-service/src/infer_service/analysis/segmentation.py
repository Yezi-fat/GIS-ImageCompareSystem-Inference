"""要素识别编排（设计 §3.3.1，FR-6，P-012 伪模型跑通 / P-014 真实模型）。

run_segmentation(req)
  ⓪ 层级路由（V1.2，工作任务十一）：请求带 tile_z 且未显式指定 model_name 时，
     z ≤ 阈值路由到低层级模型（SegFormer-RS，低分辨率瓦片层级），
     z > 阈值用默认模型（YOLO 系）；显式 model_name 恒优先（FR-3.5）
  ① loader 读图 → ② tiler 切分 → ③ engine.segment 逐批推理 → ④ 融合拼接
  ⑤ 按 class_mapping 提取选定类别的二值蒙版
     （elements 超模型类别 → UnsupportedElementError，FR-6.6）
  ⑥ postprocess → ⑦ colorize（合成 + 独立蒙版）
  ⑧ 统计：各类 area_px / ratio / patch_count（FR-6.5）；
     有 geo_transform 时按 CGCS2000 椭球逐行积分换算 area_m2（cv_common.geo）

签名说明：provider_hint 为 M2 扩展的可选参数（M1 骨架签名扩展，
透传 X-Inference-Provider 头，设计 §2.3 已同步）。
"""
from __future__ import annotations

import time

import numpy as np
import structlog

from cv_common.imaging import encode_png_b64
from cv_common.errors import InputTooLargeError, UnsupportedElementError
from cv_common.geo import pixel_area_m2
from cv_common.schemas.segmentation import (
    ClassStatistics,
    SegmentationRequest,
    SegmentationResponse,
)

from infer_service.config import settings
from infer_service.core import concurrency
from infer_service.engines import registry
from infer_service.pipeline import colorize, loader, postprocess, preprocess, tiler

logger = structlog.get_logger(__name__)


class _Route:
    """层级路由结果（V1.2）：目标模型 + 生效类别映射 + 预处理归一化参数。"""

    def __init__(
        self,
        model_name: str | None,
        model_version: str | None,
        class_mapping: dict[str, int],
        normalize_mean: list[float] | None,
        normalize_std: list[float] | None,
        routed_by: str,
    ):
        self.model_name = model_name
        self.model_version = model_version
        self.class_mapping = class_mapping
        self.normalize_mean = normalize_mean
        self.normalize_std = normalize_std
        self.routed_by = routed_by  # explicit / low-zoom / default


def _route_model(req: SegmentationRequest) -> _Route:
    """层级路由（工作任务十一）：显式 model_name > tile_z ≤ 阈值 → 低层级模型 > 默认。

    - 显式指定 model_name 或 model_version：不参与路由（FR-3.5 管理优先级最高）；
    - tile_z ≤ MODELS__ROUTING__ZOOM_THRESHOLD：低层级模型（SegFormer-RS），
      类别映射取请求 low_zoom_class_mapping，缺省用 L1 配置兜底；
    - 其余（z > 阈值或未带 tile_z）：默认模型（YOLO 系），不路由。
    """
    routing = settings.models.routing
    if req.model_name or req.model_version:
        return _Route(req.model_name, req.model_version, req.class_mapping, None, None, "explicit")
    if req.tile_z is not None and req.tile_z <= routing.zoom_threshold:
        return _Route(
            routing.low_zoom_name,
            routing.low_zoom_version,
            req.low_zoom_class_mapping or routing.low_zoom_class_mapping,
            routing.low_zoom_normalize_mean,
            routing.low_zoom_normalize_std,
            "low-zoom",
        )
    return _Route(None, None, req.class_mapping, None, None, "default")


def run_segmentation(req: SegmentationRequest, provider_hint: str | None = None) -> SegmentationResponse:
    """要素识别全管线编排（流程见模块 docstring）。"""
    started = time.perf_counter()

    route = _route_model(req)
    engine = registry.get_engine(provider_hint)
    info = engine.info_for("segmentation", route.model_name, route.model_version)  # type: ignore[attr-defined]
    _validate_elements(req, route, info.class_ids)

    # ① 读图（geo_transform：GeoTIFF 内嵌优先，PNG 由 geo_extent 推导，评审 D-01）
    image, geo_transform = loader.load_image(req.image_url, req.geo_extent)
    height, width = image.shape[:2]

    # 输入规模防御（P-025）：local-cpu 模式超 local_cpu_max_input 拒绝（INPUT_TOO_LARGE，
    # 需求 7.7 R-04 语义——Java 侧提前拦截为主、本服务兜底）
    actual_provider = getattr(engine, "actual_provider", info.provider)
    if actual_provider == "local-cpu" and max(height, width) > settings.limits.local_cpu_max_input:
        raise InputTooLargeError(
            f"输入影像 {width}x{height} 超本地 CPU 模式可处理上限 "
            f"{settings.limits.local_cpu_max_input}px（请改用远程/GPU 提供方或缩小范围）"
        )

    # ②③④ 流式切分 → 推理 → 均值融合（设计 §5：不同时持有全部 Tile；信号量限流 P-018）
    channels = (max(info.class_ids) + 1) if info.class_ids else (max(route.class_mapping.values()) + 1)
    canvas, count = tiler.alloc_canvas(height, width, channels)
    tile_count = 0
    semaphore = concurrency.get_inference_semaphore(
        getattr(engine, "actual_provider", info.provider)
    )
    with semaphore:
        for tile, offset in tiler.split_tiles(image, settings.tiling.tile_size, settings.tiling.overlap):
            prob = engine.segment(
                [preprocess.preprocess_tile(tile, route.normalize_mean, route.normalize_std)],
                route.model_name,
                route.model_version,
            )[0]
            tiler.fuse_tile(canvas, prob, offset, count)
            tile_count += 1
    probs = tiler.finalize_canvas(canvas, count)
    label_map = probs.argmax(axis=0)  # [H,W] 类别 ID

    # ⑤⑥⑦ 按生效映射提取 → 后处理 → 着色
    per_class_masks: dict[str, np.ndarray] = {}
    for element in req.elements:
        mask = (label_map == route.class_mapping[element]).astype(np.uint8)
        per_class_masks[element] = postprocess.postprocess_mask(mask, req.min_area)
    combined = colorize.colorize_combined(per_class_masks, req.colors)

    # ⑧ 统计（area_m2：经纬度网格像素面积仅随纬度变化，按行积分）
    statistics = {
        element: _element_statistics(mask, geo_transform, height, width)
        for element, mask in per_class_masks.items()
    }

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "segmentation_done",
        elements=req.elements,
        tiles=tile_count,
        image_size=f"{width}x{height}",
        routed_by=route.routed_by,
        model=info.name,
        model_version=info.version,
        tile_z=req.tile_z,
        actual_provider=actual_provider,
        elapsed_ms=elapsed_ms,
    )
    return SegmentationResponse(
        combined_mask_png_b64=encode_png_b64(combined),
        per_class_masks={e: encode_png_b64((m * 255).astype(np.uint8)) for e, m in per_class_masks.items()},
        statistics=statistics,
        geo_transform=geo_transform,
        model_info=info,
        actual_provider=actual_provider,
        elapsed_ms=elapsed_ms,
    )


def _validate_elements(
    req: SegmentationRequest, route: _Route, class_ids: list[int] | None
) -> None:
    """FR-6.6：选定类别超模型可识别范围时返回明确错误，不静默忽略。"""
    unknown = [e for e in req.elements if e not in route.class_mapping]
    if unknown:
        mapping_desc = (
            "low_zoom_class_mapping（低层级路由）" if route.routed_by == "low-zoom" else "class_mapping"
        )
        raise UnsupportedElementError(
            f"要素类别 {unknown} 不在{mapping_desc}中（可识别：{sorted(route.class_mapping)}）"
        )
    if class_ids is not None:
        out_of_model = [e for e in req.elements if route.class_mapping[e] not in class_ids]
        if out_of_model:
            raise UnsupportedElementError(
                f"要素类别 {out_of_model} 超出模型输出类别 {class_ids}（FR-6.6）"
            )


def _element_statistics(
    mask: np.ndarray, geo_transform: list[float] | None, height: int, width: int
) -> ClassStatistics:
    """单类统计：area_px / ratio / patch_count / area_m2（CGCS2000 椭球逐行积分）。"""
    import cv2

    area_px = int(mask.sum())
    ratio = area_px / (height * width) if height * width else 0.0
    patch_count = 0
    if area_px > 0:
        num, _ = cv2.connectedComponents(mask, connectivity=8)
        patch_count = num - 1
    area_m2: float | None = None
    if geo_transform is not None and area_px > 0:
        rows = np.nonzero(mask.any(axis=1))[0]
        area_m2 = float(sum(pixel_area_m2(geo_transform, int(r), 0) * int(mask[r].sum()) for r in rows))
    return ClassStatistics(area_px=area_px, area_m2=area_m2, ratio=ratio, patch_count=patch_count)
