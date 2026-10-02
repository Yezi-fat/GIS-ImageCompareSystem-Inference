"""nlp-service L1 部署配置（设计 §6）：LLM/地理编码完整 URL、key、provider、熔断参数。

URL 完整性（需求约束 #10 / V2.3）：llm_url 为完整 chat completions 端点
（httpx 直调，不经 SDK 路径拼接）；geocoder_url 为完整地理编码端点
（仅按各 provider 协议拼接 query 参数）；空 = 能力不可用（正常部署形态）。
启动时校验各 URL 可解析为绝对地址，非法即拒绝启动。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _validate_url(v: str) -> str:
    """URL 配置项启动校验：空串表示未配置；非空须为 http/https 绝对地址。"""
    if v and not v.startswith(("http://", "https://")):
        raise ValueError(f"URL 配置项须为完整绝对地址（含 http/https scheme），当前值：{v!r}")
    return v


class SecuritySettings(BaseModel):
    """FR-10.7 安全节结构视图（对齐设计 §6）：由顶层环境字段组装。

    注：pydantic-settings 嵌套模型的 validation_alias 解析不可靠（多字段时失效），
    故 AUTH_ENABLED/INTERNAL_TOKEN 以顶层扁平字段承接环境变量（与 Java 同源注入）。
    """

    auth_enabled: bool = True
    internal_token: str = ""


class ServiceSettings(BaseModel):
    port: int = 8003
    log_level: str = "INFO"


class CircuitSettings(BaseModel):
    """进程内熔断器参数（FR-9.7，V2.2）。"""

    failure_threshold: int = Field(default=5, gt=0, description="窗口内连续失败次数触发断流")
    window_s: int = Field(default=60, gt=0, description="失败计数窗口（秒）")
    open_duration_s: int = Field(default=30, gt=0, description="断流时长，之后半开试探")


class NlpSettings(BaseModel):
    llm_url: str = Field(
        default="",
        description="完整 chat completions 端点（如 http://llm.internal/ai/gw/v1/chat/completions）；"
                    "空 = LLM 不可用 → 常驻规则解析",
    )
    llm_model: str = "qwen2.5-14b-instruct"
    llm_timeout_s: int = Field(default=15, gt=0, description="单次 LLM 调用超时，超时即当次降级（FR-9.7）")
    circuit: CircuitSettings = CircuitSettings()
    geocoder_provider: str = Field(default="nominatim", description="amap / baidu / nominatim")
    geocoder_url: str = Field(default="", description="完整地理编码端点 URL（查询参数按 provider 协议拼接）")
    geocoder_key: str = ""

    _check_llm_url = field_validator("llm_url")(_validate_url)
    _check_geocoder_url = field_validator("geocoder_url")(_validate_url)


class Settings(BaseSettings):
    """nlp-service 配置根。环境变量嵌套分隔符 `__`。"""

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    # FR-10.7：与 Java 侧 security.auth.enabled 同一总开关，同一环境变量注入
    auth_enabled: bool = True
    internal_token: str = ""

    service: ServiceSettings = ServiceSettings()
    nlp: NlpSettings = NlpSettings()

    @property
    def security(self) -> SecuritySettings:
        return SecuritySettings(auth_enabled=self.auth_enabled, internal_token=self.internal_token)


settings = Settings()
