"""远程推理引擎（设计 §3.1.3，FR-1.9/5.2，P-017 实现）。

- httpx 连接通用推理服务；**端点为完整 URL，直接 POST，不拼接路径**
  （需求约束 #10 / V2.3）；
- Tile 批量提交（FR-1.9）：按 batch_size 分组调用，批量失败自动降级为逐块重试一次；
- 本层只做单次调用超时控制（timeout=25s，略小于 Java 侧 30s），
  外层熔断由 Java Resilience4j 负责。

线上协议（与通用推理服务方固化的接口规范，FR-5.4 联调文档落稿前的工作口径）：
  请求  POST {endpoint}
        {"task": "segmentation"|"change_detection", "model_name", "model_version",
         "tiles": ["<png base64>", ...],                          // 分割
         "tiles_before": [...], "tiles_after": [...]}             // 变化检测
  响应  {"probs": [{"shape": [C,H,W], "data_b64": "<float32 原始字节 base64>"}, ...]}
契约测试经 respx 打桩验证批量/重试逻辑；协议字段如与平台方联调后不一致，
仅改 _post_batch 一处，编排层无感。
"""
from __future__ import annotations

import base64
import io

import httpx
import numpy as np
import structlog

from cv_common.errors import InferenceFailedError
from cv_common.imaging import encode_png_b64
from cv_common.schemas.health import ModelInfo

from infer_service.config import Settings

logger = structlog.get_logger(__name__)


class RemoteEngine:
    """通用推理服务适配器（FR-5.2 远程适配器）。"""

    def __init__(self, settings: Settings):
        """端点/超时/批量由配置注入（Java 侧 FR-3.4 切换版本经请求下发）。"""
        if not settings.remote.endpoint:
            raise InferenceFailedError(
                "远程推理端点未配置（remote.endpoint 为空）——未配置远程属正常形态，"
                "请经 provider 提示选择 local-gpu/local-cpu"
            )
        self._endpoint = settings.remote.endpoint
        self._timeout_s = settings.remote.timeout_s
        self._batch_size = settings.remote.batch_size
        self._settings = settings

    def segment(
        self,
        tiles: list[np.ndarray],
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> list[np.ndarray]:
        """批量提交分割 Tile → 各类别概率图 [C,H,W]。"""
        name, version = self._resolve("segmentation", model_name, model_version)
        return self._infer_batched(
            task="segmentation",
            payloads=[{"tiles": [_tile_to_b64(t)]} for t in tiles],
            model_name=name,
            model_version=version,
        )

    def detect_change(
        self,
        tiles_before: list[np.ndarray],
        tiles_after: list[np.ndarray],
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> list[np.ndarray]:
        """批量提交双时相 Tile → 变化概率图 [1,H,W]。"""
        name, version = self._resolve("change_detection", model_name, model_version)
        return self._infer_batched(
            task="change_detection",
            payloads=[
                {"tiles_before": [_tile_to_b64(b)], "tiles_after": [_tile_to_b64(a)]}
                for b, a in zip(tiles_before, tiles_after, strict=True)
            ],
            model_name=name,
            model_version=version,
        )

    def info(self) -> ModelInfo:
        """远程模型名/版本（配置注入值）；provider 恒为 remote。"""
        name, version = self._resolve("segmentation", None, None)
        return ModelInfo(
            name=name,
            version=version,
            provider="remote",
            class_ids=list(self._settings.models.segmentation.class_ids),
        )

    @property
    def actual_provider(self) -> str:
        return "remote"

    def info_for(self, model_type: str, model_name: str | None, model_version: str | None) -> ModelInfo:
        name, version = self._resolve(model_type, model_name, model_version)
        class_ids = (
            list(self._settings.models.segmentation.class_ids)
            if model_type == "segmentation"
            else None
        )
        return ModelInfo(name=name, version=version, provider="remote", class_ids=class_ids)

    # ---- 内部实现 ----

    def _resolve(self, model_type: str, model_name: str | None, model_version: str | None) -> tuple[str, str]:
        default = getattr(self._settings.models, model_type)
        return model_name or default.name, model_version or default.version

    def _infer_batched(
        self,
        task: str,
        payloads: list[dict],
        model_name: str,
        model_version: str,
    ) -> list[np.ndarray]:
        """按 batch_size 分组提交；批量失败降级为逐块重试一次（FR-1.9）。"""
        results: list[np.ndarray] = []
        for start in range(0, len(payloads), self._batch_size):
            group = payloads[start : start + self._batch_size]
            try:
                results.extend(self._post_batch(task, group, model_name, model_version))
            except InferenceFailedError:
                logger.warning(
                    "remote_batch_failed_fallback_single",
                    batch_start=start,
                    batch_size=len(group),
                )
                for payload in group:  # 逐块重试一次
                    results.extend(self._post_batch(task, [payload], model_name, model_version))
        return results

    def _post_batch(
        self,
        task: str,
        payloads: list[dict],
        model_name: str,
        model_version: str,
    ) -> list[np.ndarray]:
        """单批 POST（完整端点直调，不拼接路径，约束 #10）。"""
        # 合并组内 Tile：{"tiles": [...]} 逐键合并为列表
        merged: dict[str, list] = {}
        for payload in payloads:
            for key, value in payload.items():
                merged.setdefault(key, []).extend(value)
        body = {
            "task": task,
            "model_name": model_name,
            "model_version": model_version,
            **merged,
        }
        try:
            resp = httpx.post(self._endpoint, json=body, timeout=self._timeout_s)
        except httpx.TimeoutException as e:
            raise InferenceFailedError(f"远程推理超时（{self._timeout_s}s）：{e}") from e
        except httpx.HTTPError as e:
            raise InferenceFailedError(f"远程推理调用失败：{type(e).__name__}: {e}") from e
        if resp.status_code != 200:
            raise InferenceFailedError(f"远程推理返回 HTTP {resp.status_code}：{resp.text[:200]}")
        return _parse_probs(resp.json())


def _tile_to_b64(tile: np.ndarray) -> str:
    """预处理后的 CHW float32 Tile → 可传输标量：先转回 HWC uint8 再 PNG base64。

    注：当前伪模型联调口径（有损但足够跑通链路）；与平台方固化接口规范时
    （FR-5.4）可能改为 float32 原始字节传输，仅改本函数与 _parse_probs。
    """
    arr = tile
    if arr.dtype != np.uint8:
        arr = np.clip(arr.transpose(1, 2, 0) * 255.0, 0, 255).astype(np.uint8)
    return encode_png_b64(arr)


def _parse_probs(data: dict) -> list[np.ndarray]:
    """解析响应 probs：[{"shape": [C,H,W], "data_b64": float32 原始字节}...]。"""
    probs = []
    for item in data.get("probs", []):
        shape = tuple(item["shape"])
        raw = base64.b64decode(item["data_b64"])
        probs.append(np.frombuffer(raw, dtype=np.float32).reshape(shape).copy())
    return probs
