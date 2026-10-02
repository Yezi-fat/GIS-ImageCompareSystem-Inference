"""compute-service L1 部署配置（设计 §6）：仅端口、日志级别与安全开关，无模型。

业务策略参数（diff 分色、min_area、geo_transform）逐请求随 body 下发（FR-10）。
"""
from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class SecuritySettings(BaseModel):
    """FR-10.7 安全节结构视图（对齐设计 §6）：由顶层环境字段组装。

    注：pydantic-settings 嵌套模型的 validation_alias 解析不可靠（多字段时失效），
    故 AUTH_ENABLED/INTERNAL_TOKEN 以顶层扁平字段承接环境变量（与 Java 同源注入）。
    """

    auth_enabled: bool = True
    internal_token: str = ""


class ServiceSettings(BaseModel):
    port: int = 8002
    log_level: str = "INFO"


class Settings(BaseSettings):
    """compute-service 配置根。环境变量嵌套分隔符 `__`。"""

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    # FR-10.7：与 Java 侧 security.auth.enabled 同一总开关，同一环境变量注入
    auth_enabled: bool = True
    internal_token: str = ""

    service: ServiceSettings = ServiceSettings()

    @property
    def security(self) -> SecuritySettings:
        return SecuritySettings(auth_enabled=self.auth_enabled, internal_token=self.internal_token)


settings = Settings()
