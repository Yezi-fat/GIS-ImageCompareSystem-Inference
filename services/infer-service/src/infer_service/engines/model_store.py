"""模型存储与加载（设计 §2.3 model_store，FR-3.2/3.5，评审 P-02，P-012/P-014 实现）。

模型文件按 {models.dir}/{name}/{version}/{fp32|int8}.onnx 组织；
引擎按名+版本懒加载并缓存会话——Java 侧"激活新版本"= 改 L2 配置热生效后
随请求下发 model_name/model_version，infer-service 无需重启、无需被显式通知；
模型文件经对象存储 + 命名卷下发（J-05 约定待 Java 确认）。

实现里程碑：P-012（本地加载链路随伪模型落地；真实模型规范接入属 P-014）。
"""
from __future__ import annotations

import os
import threading
from typing import Any, Literal

from cv_common.errors import InferenceFailedError, ModelNotReadyError

from infer_service.config import Settings

ModelType = Literal["segmentation", "change_detection"]


class ModelStore:
    """ONNX 会话缓存：键 (model_type, name, version, provider)，值 InferenceSession。"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._cache: dict[tuple[str, str, str, str], Any] = {}
        self._lock = threading.Lock()

    def resolve_version(
        self,
        model_name: str | None,
        model_version: str | None,
        model_type: ModelType,
    ) -> tuple[str, str]:
        """解析请求（可空）名/版本 → 实际 (name, version)；缺省用 L1 配置默认版本。"""
        default = getattr(self._settings.models, model_type)
        return model_name or default.name, model_version or default.version

    def get_session(
        self,
        model_type: ModelType,
        model_name: str | None,
        model_version: str | None,
        provider: str,
    ) -> Any:
        """按名+版本懒加载会话（FP32/INT8 按 provider 选择，FR-3.2）。

        模型文件缺失 → ModelNotReadyError；GPU 会话创建失败由调用方（引擎）回退 CPU。
        """
        import onnxruntime as ort

        name, version = self.resolve_version(model_name, model_version, model_type)
        key = (model_type, name, version, provider)
        with self._lock:
            if key in self._cache:
                return self._cache[key]

            precision = "fp32" if provider == "local-gpu" else "int8"
            filename = getattr(getattr(self._settings.models, model_type), precision)
            path = os.path.join(self._settings.models.dir, name, version, filename)
            if not os.path.isfile(path):
                raise ModelNotReadyError(
                    f"模型文件不存在：{path}（按 {{name}}/{{version}}/{{fp32|int8}}.onnx 组织，"
                    f"模型下发约定见 J-05）"
                )
            if provider == "local-gpu":
                providers = ["CUDAExecutionProvider"]
                sess_options = ort.SessionOptions()
            else:
                providers = ["CPUExecutionProvider"]
                sess_options = ort.SessionOptions()
                # intra_op_num_threads = 核数 // 槽数，避免多路推理争抢（设计 §5）
                slots = self._settings.concurrency.cpu_slots
                sess_options.intra_op_num_threads = max(1, (os.cpu_count() or 1) // slots)
            session = ort.InferenceSession(path, sess_options=sess_options, providers=providers)
            # GPU 路径须核验 provider 实际生效（onnxruntime 对不可用 provider 仅告警不报错，
            # 静默回退会导致 actual_provider 标记失真，P-015）
            if provider == "local-gpu" and "CUDAExecutionProvider" not in session.get_providers():
                raise InferenceFailedError(
                    f"GPU 会话创建后实际提供方不含 CUDAExecutionProvider"
                    f"（实际：{session.get_providers()}），按 GPU 不可用处理"
                )
            self._cache[key] = session
            return session

    def loaded_models(self) -> dict[tuple[str, str, str, str], Any]:
        """已加载会话快照（/health 模型就绪状态数据源，FR-5.5）。"""
        with self._lock:
            return dict(self._cache)
