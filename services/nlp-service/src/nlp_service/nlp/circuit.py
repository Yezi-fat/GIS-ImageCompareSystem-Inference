"""进程内熔断器（设计 §2.5 circuit，FR-9.7，P-024 实现）。

60s 窗口内连续失败 N 次（默认 5）→ 断流 open_duration（默认 30s）→
半开试探一次，成功则闭合；参数经 L1 配置注入（config.CircuitSettings）。
状态经 /health 上报（nlu.circuit），供 Java 侧监控与告警（设计 §3.5）。

线程安全（多请求并发）；clock 可注入便于测试。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Literal

CircuitState = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    """LLM 调用熔断器：防止连续故障拖慢服务（FR-9.7）。"""

    def __init__(
        self,
        failure_threshold: int = 5,
        window_s: int = 60,
        open_duration_s: int = 30,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._threshold = failure_threshold
        self._window_s = window_s
        self._open_duration_s = open_duration_s
        self._clock = clock
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        self._half_open_probe = False  # 半开态是否已有试探请求在途
        self._lock = threading.RLock()

    def allow(self) -> bool:
        """当前是否允许发起 LLM 调用（open 未到半开时机 → False；半开仅放行一次试探）。"""
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at < self._open_duration_s:
                return False
            if self._half_open_probe:
                return False  # 半开只允许一个试探
            self._half_open_probe = True
            return True

    def on_success(self) -> None:
        """调用成功：清零失败计数，断流/半开态闭合。"""
        with self._lock:
            self._failures.clear()
            self._opened_at = None
            self._half_open_probe = False

    def on_failure(self) -> None:
        """调用失败：窗口内计数达阈值断流；半开试探失败立即重新断流。"""
        now = self._clock()
        with self._lock:
            if self._opened_at is not None:
                # 半开试探失败 → 重新断流计时
                self._opened_at = now
                self._half_open_probe = False
                return
            self._failures.append(now)
            while self._failures and now - self._failures[0] > self._window_s:
                self._failures.popleft()
            if len(self._failures) >= self._threshold:
                self._opened_at = now
                self._failures.clear()

    def state(self) -> CircuitState:
        """当前状态 closed / open / half_open，供 /health 上报。"""
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if self._clock() - self._opened_at >= self._open_duration_s:
                return "half_open"
            return "open"
