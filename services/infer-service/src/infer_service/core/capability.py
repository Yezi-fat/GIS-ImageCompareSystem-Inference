"""推理环境探测（设计 §2.3 capability，FR-5.5/5.7，P-013 实现）。

/health 数据源：CUDA 可用性（onnxruntime.get_available_providers() +
试建小会话验证）、显存（pynvml，GPU 版镜像内置）、CPU 核数、模型加载状态。
无 GPU 环境 gpu_available=false 且不影响启动（P-013 验收）。

onnxruntime/pynvml 函数内延迟导入；探测结果进程内缓存（环境运行期不变）。
"""
from __future__ import annotations

import os
import threading

_cache: dict[str, object] = {}
_lock = threading.RLock()  # 可重入：gpu_usable 探测链会嵌套调用 gpu_available


def _cached(key: str, probe):
    if key not in _cache:
        with _lock:
            if key not in _cache:
                _cache[key] = probe()
    return _cache[key]


def gpu_available() -> bool:
    """CUDA ExecutionProvider 是否在可用列表中。"""

    def _probe() -> bool:
        try:
            import onnxruntime as ort

            return "CUDAExecutionProvider" in ort.get_available_providers()
        except Exception:
            return False

    return bool(_cached("gpu_available", _probe))


def gpu_usable() -> bool:
    """试建小会话验证 GPU 实际可用（驱动/显存满足）。"""

    def _probe() -> bool:
        if not gpu_available():
            return False
        try:
            import numpy as np
            import onnxruntime as ort
            from onnx import TensorProto, helper

            # 最小恒等图：input[1] → output[1]
            graph = helper.make_graph(
                [helper.make_node("Identity", ["x"], ["y"])],
                "gpu-probe",
                [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])],
                [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1])],
            )
            model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
            model.ir_version = 8
            session = ort.InferenceSession(model.SerializeToString(), providers=["CUDAExecutionProvider"])
            session.run(None, {"x": np.array([1.0], dtype=np.float32)})
            return True
        except Exception:
            return False

    return bool(_cached("gpu_usable", _probe))


def vram_mb() -> int | None:
    """显存容量（pynvml）；无 GPU 或非 GPU 镜像返回 None。"""

    def _probe() -> int | None:
        try:
            import pynvml

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            return int(pynvml.nvmlDeviceGetMemoryInfo(handle).total // (1024 * 1024))
        except Exception:
            return None

    return _cached("vram_mb", _probe)  # type: ignore[return-value]


def cpu_cores() -> int:
    """CPU 核数。"""
    return os.cpu_count() or 1
