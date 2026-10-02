"""结构化日志（设计 §2.2，P-009 实现）：structlog JSON + 任务 ID 全链路透传。

与 Java 侧日志格式对齐（需求 8.4）：JSON 输出 stdout，含 service 字段；
X-Task-Id / X-Trace-Id 透传进日志上下文，多期任务日志带期次标签
（期次标签由编排层 log.bind(period=...) 按需绑定）。
"""
from __future__ import annotations

import logging
import sys

import structlog
from fastapi import FastAPI, Request


def configure_logging(service_name: str) -> None:
    """structlog JSON 日志配置，注入 service 字段（infer/compute/nlp 三服务区分）。"""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    # service 字段全服务恒定，直接绑进默认上下文
    structlog.contextvars.bind_contextvars(service=service_name)


def task_id_middleware(app: FastAPI) -> None:
    """X-Task-Id / X-Trace-Id 请求头透传进 structlog 日志上下文。"""

    @app.middleware("http")
    async def _task_id_context(request: Request, call_next):
        structlog.contextvars.clear_contextvars()
        # service 字段在 configure_logging 时已绑默认上下文，clear 后需重绑
        structlog.contextvars.bind_contextvars(
            service=app.title,
            task_id=request.headers.get("X-Task-Id"),
            trace_id=request.headers.get("X-Trace-Id"),
        )
        try:
            return await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
