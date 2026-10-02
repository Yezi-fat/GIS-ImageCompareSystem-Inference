"""内部令牌鉴权中间件（V2.1 新增，FR-10.7，P-009 实现）。

security.auth_enabled 与 Java 侧 security.auth.enabled 为同一总开关，
由同一环境变量 AUTH_ENABLED 注入，保证两侧语义一致。

两态行为（P-009 验收）：
- enabled=True：校验请求头 X-Internal-Token（与 Java 共享密钥），失败 401 UNAUTHORIZED；
- enabled=False：直接放行（任何人可用，全内网受信部署形态）；
- /health 恒豁免（健康探测不带令牌）。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# 恒豁免路径（健康探测/契约文档）
_EXEMPT_PATHS = {"/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def auth_middleware(app: FastAPI, *, enabled: bool, internal_token: str) -> None:
    """内部令牌校验中间件挂载（语义见模块 docstring）。"""

    @app.middleware("http")
    async def _internal_token_auth(request: Request, call_next):
        if not enabled or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        token = request.headers.get("X-Internal-Token", "")
        if not internal_token or token != internal_token:
            return JSONResponse(
                status_code=401,
                content={"code": "UNAUTHORIZED", "message": "内部令牌缺失或非法"},
            )
        return await call_next(request)
