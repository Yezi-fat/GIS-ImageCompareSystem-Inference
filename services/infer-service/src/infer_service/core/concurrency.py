"""推理并发控制（设计 §5，P-018 实现）。

实现口径（评审 D-06）：推理路由为同步 def、由 FastAPI 线程池执行，
故并发信号量落地为 **threading.Semaphore**（而非 asyncio.Semaphore），
消除同步工作线程跨事件循环 acquire 的问题。

CPU 默认 2 槽 / GPU 默认 4 槽（L1 配置），超并发请求排队，防内存/显存打爆；
intra_op_num_threads = cpu_cores // 槽数（ModelStore 建会话时设置），
避免多路推理争抢。
"""
from __future__ import annotations

import threading

from infer_service.config import settings

_semaphores: dict[str, threading.Semaphore] = {}
_lock = threading.Lock()


def get_inference_semaphore(provider: str) -> threading.Semaphore:
    """按 provider 返回进程级推理信号量（懒初始化，槽位数取 L1 配置）。

    remote 路径同样受限（Tile 数据在本服务内存中组装，防内存打爆）。
    """
    if provider in _semaphores:
        return _semaphores[provider]
    with _lock:
        if provider not in _semaphores:
            slots = (
                settings.concurrency.gpu_slots
                if provider == "local-gpu"
                else settings.concurrency.cpu_slots
            )
            _semaphores[provider] = threading.Semaphore(slots)
        return _semaphores[provider]


def reset_semaphores() -> None:
    """清空信号量缓存（测试用；配置变更后重建）。"""
    with _lock:
        _semaphores.clear()
