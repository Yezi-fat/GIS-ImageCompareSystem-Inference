"""引擎注册与选择（设计 §3.1.4，P-012 实现）。

三级提供方解析的判定权在 Java（FR-5.6）；本服务按 provider 提示执行——
remote 提示时 Java 已判定连通，本服务不再重复探测。
"""
from __future__ import annotations

import threading

from infer_service.config import settings
from infer_service.core import capability
from infer_service.engines.base import InferenceEngine
from infer_service.engines.local_onnx import LocalOnnxEngine
from infer_service.engines.model_store import ModelStore
from infer_service.engines.remote import RemoteEngine

_engines: dict[str, InferenceEngine] = {}
_store: ModelStore | None = None
_lock = threading.RLock()  # 可重入：get_engine 持锁时会调用 _get_store


def _get_store() -> ModelStore:
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                _store = ModelStore(settings)
    return _store


def get_engine(provider_hint: str | None) -> InferenceEngine:
    """按 provider 提示选择引擎（进程内缓存，键为解析后 provider）。

    remote → RemoteEngine；local-gpu 且 GPU 可用 → GPU 会话；
    否则 CPU 兜底（引擎在响应中如实标记 actual_provider）。
    provider_hint 缺省时按本地配置 settings.service.default_provider。
    """
    provider = provider_hint or settings.service.default_provider
    if provider == "remote":
        resolved = "remote"
    elif provider == "local-gpu" and capability.gpu_usable():
        resolved = "local-gpu"
    else:
        resolved = "local-cpu"

    if resolved in _engines:
        return _engines[resolved]
    with _lock:
        if resolved not in _engines:
            if resolved == "remote":
                _engines[resolved] = RemoteEngine(settings)
            else:
                _engines[resolved] = LocalOnnxEngine(settings, _get_store(), resolved)
        return _engines[resolved]


def reset_engines() -> None:
    """清空引擎/会话缓存（测试用；配置变更后重建）。"""
    global _store
    with _lock:
        _engines.clear()
        _store = None


def model_store() -> ModelStore:
    """进程级 ModelStore（/health 模型就绪状态数据源，FR-5.5）。"""
    return _get_store()
