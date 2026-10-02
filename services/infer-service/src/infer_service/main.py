"""FastAPI 入口（设计 §2.3）：中间件、异常注册均收敛在 cv-common。

M1 骨架里程碑：装配调用已就位（cv-common 各函数 M1 为空实现，
P-009 实现后无需改动本文件）；路由返回 501 占位、/health 返回 schema 占位。
"""
from __future__ import annotations

from fastapi import FastAPI

from cv_common.auth import auth_middleware
from cv_common.errors import register_error_handlers
from cv_common.logging import configure_logging, task_id_middleware

from infer_service.config import settings
from infer_service.routers import health, infer

SERVICE_NAME = "infer-service"

configure_logging(SERVICE_NAME)

app = FastAPI(title=SERVICE_NAME, version="0.1.0")

task_id_middleware(app)
auth_middleware(
    app,
    enabled=settings.security.auth_enabled,
    internal_token=settings.security.internal_token,
)
register_error_handlers(app)

app.include_router(infer.router)
app.include_router(health.router)
