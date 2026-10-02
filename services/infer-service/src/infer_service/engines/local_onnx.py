"""本地 ONNX 引擎（设计 §3.1.2，FR-3.2/5.2）。

- local-gpu：CUDAExecutionProvider + FP32 模型；
- local-cpu：CPUExecutionProvider + INT8 量化模型，intra_op_num_threads 按
  CPU 核数/槽数配置（ModelStore 内设置）；
- 加载失败（如 GPU 版在无卡环境启动）→ 自动回退 CPU 会话，
  并在 /health 与每次响应的 actual_provider 中如实标记；
- 会话常驻内存（ModelStore 缓存预热），推理时仅做 preprocess → run；
- 多版本加载（评审 P-02）：按请求 model_name/model_version 懒加载并缓存会话。

实现里程碑：P-012（伪模型跑通全链路）/ P-014（真实模型规范接入）/ P-015（双路径一致性）。
"""
from __future__ import annotations

import numpy as np
import structlog

from cv_common.errors import InferenceFailedError
from cv_common.schemas.health import ModelInfo

from infer_service.config import Settings
from infer_service.engines.model_store import ModelStore, ModelType

logger = structlog.get_logger(__name__)


class LocalOnnxEngine:
    """本地推理引擎：CUDA FP32 / CPU INT8 双路径（FR-3.2）。"""

    def __init__(self, settings: Settings, model_store: ModelStore, target_provider: str):
        """target_provider: local-gpu / local-cpu（GPU 加载失败自动回退 CPU 并标记）。"""
        self._settings = settings
        self._store = model_store
        self._requested_provider = target_provider
        self._actual_provider = target_provider
        self._gpu_failed = False  # 首次 GPU 失败后不再尝试（熔断式回退）

    @property
    def actual_provider(self) -> str:
        """实际执行提供方（与请求不一致时如实标记，降级场景）。"""
        return self._actual_provider

    def _session(self, model_type: ModelType, model_name: str | None, model_version: str | None):
        provider = self._requested_provider
        if provider == "local-gpu" and self._gpu_failed:
            provider = "local-cpu"
        try:
            session = self._store.get_session(model_type, model_name, model_version, provider)
        except Exception:
            if provider != "local-gpu":
                raise
            # GPU 会话创建失败 → 回退 CPU 会话（FR-5.2 降级路径），如实标记
            logger.warning("gpu_session_failed_fallback_cpu", model_type=model_type)
            self._gpu_failed = True
            self._actual_provider = "local-cpu"
            session = self._store.get_session(model_type, model_name, model_version, "local-cpu")
        return session

    def segment(
        self,
        tiles: list[np.ndarray],
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> list[np.ndarray]:
        """语义分割 Tile 批次推理 → 各类别概率图 [C,H,W]。

        双输出格式适配（P-014）：
        - [1, C, H, W] 逐像素概率图（分割模型/伪模型）→ 直接取用；
        - [1, 4+C, anchors] 检测框（YOLO 系，如 yolo11）→ yolo_decode 栅格化为
          [1+C, H, W] 概率图（通道 0=背景，通道 k=COCO 类别 k-1）；
        解码后通道数与配置 class_ids 一致性校验（不一致说明模型与配置错配）。
        """
        session = self._session("segmentation", model_name, model_version)
        input_name = session.get_inputs()[0].name
        seg_cfg = self._settings.models.segmentation
        results: list[np.ndarray] = []
        for tile in tiles:
            raw = session.run(None, {input_name: tile[np.newaxis, ...]})[0]
            if raw.ndim == 4:        # 逐像素概率图（分割模型/伪模型）
                prob = raw[0]
            elif raw.ndim == 3:      # 检测框格式（YOLO 系）
                from infer_service.engines.yolo_decode import decode_yolo_detections

                height, width = tile.shape[1], tile.shape[2]
                prob = decode_yolo_detections(
                    raw[0], height, width, seg_cfg.conf_threshold, seg_cfg.nms_threshold
                )
            else:
                raise InferenceFailedError(
                    f"模型输出维度不支持：ndim={raw.ndim}（支持逐像素概率图 [1,C,H,W] "
                    f"与检测框 [1,4+C,anchors] 两种格式）"
                )
            if seg_cfg.class_ids and prob.shape[0] != max(seg_cfg.class_ids) + 1:
                raise InferenceFailedError(
                    f"模型输出通道数 {prob.shape[0]} 与配置 class_ids（max={max(seg_cfg.class_ids)}，"
                    f"期望通道数 {max(seg_cfg.class_ids) + 1}）不一致——"
                    f"模型与 MODELS__SEGMENTATION__CLASS_IDS 配置错配"
                )
            results.append(prob)
        return results

    def detect_change(
        self,
        tiles_before: list[np.ndarray],
        tiles_after: list[np.ndarray],
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> list[np.ndarray]:
        """双时相 Tile 批次推理 → 变化概率图 [1,H,W]。TODO(P-016 编排接入）。"""
        session = self._session("change_detection", model_name, model_version)
        names = [i.name for i in session.get_inputs()]
        return [
            session.run(None, {names[0]: b[np.newaxis, ...], names[1]: a[np.newaxis, ...]})[0][0]
            for b, a in zip(tiles_before, tiles_after, strict=True)
        ]

    def info(self) -> ModelInfo:
        """分割模型信息（Protocol 默认口径）；变化检测模型用 info_for。"""
        return self.info_for("segmentation")

    def info_for(
        self,
        model_type: ModelType,
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> ModelInfo:
        """指定模型类型的名/版本/provider/类别表（FR-3.3 追溯数据源）。"""
        name, version = self._store.resolve_version(model_name, model_version, model_type)
        class_ids = None
        if model_type == "segmentation":
            class_ids = list(self._settings.models.segmentation.class_ids)
        return ModelInfo(name=name, version=version, provider=self._actual_provider, class_ids=class_ids)
