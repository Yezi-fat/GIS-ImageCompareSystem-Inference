"""LLM 客户端（设计 §3.4，FR-9.1/9.3/9.7，P-021 实现）。

httpx 直接 POST 配置的完整端点 llm_url（请求体遵循 OpenAI chat completions 协议），
不经 SDK 路径拼接（URL 完整性约束，需求约束 #10）；
prompt 注入要素目录与意图枚举，结构化输出
{intent, location_text, elements, periods, confidence}；
schema 校验失败重试 2 次，耗尽后抛 LlmDegradeSignal（不当请求错误抛出，
由 parser 当次落规则解析，FR-9.7）；单次调用超时 llm_timeout_s（默认 15s）。

实现说明：设计 V1.x 提到 instructor 约束结构化输出——instructor 依赖 OpenAI SDK
客户端对象，与“httpx 直调完整 URL”（约束 #10，V2.3）不兼容，故以
response_format=json_object + Pydantic 校验重试手工实现同等约束（P-021 口径）。
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from cv_common.schemas.nlp import ElementCatalogItem

from nlp_service.config import settings


class LlmDegradeSignal(Exception):
    """LLM 调用失败需当次降级规则解析的信号（FR-9.7）：超时/5xx/连接失败/schema 重试耗尽。"""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason  # timeout | error | schema_invalid


class _LlmExtraction(BaseModel):
    """LLM 结构化输出 schema（校验失败重试，耗尽降级）。"""

    intent: str | None = Field(default=None)
    location_text: str | None = Field(default=None)
    elements: list[str] = Field(default_factory=list)
    periods: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


_INTENTS = "feature_extraction（要素识别）/ feature_comparison（双期差异比对）/ temporal_analysis（多期时序分析）"
_SCHEMA_RETRY = 2  # schema 校验失败重试 2 次（共 3 次调用）


def extract_by_llm(text: str, element_catalog: list[ElementCatalogItem]) -> dict:
    """LLM 结构化抽取 → {intent, location_text, elements, periods, confidence}。

    失败抛 LlmDegradeSignal（由 parser 当次降级，FR-9.7）。
    """
    if not settings.nlp.llm_url:
        raise LlmDegradeSignal("error", "LLM 未配置（llm_url 为空）")

    body = {
        "model": settings.nlp.llm_model,
        "messages": [
            {"role": "system", "content": _build_prompt(element_catalog)},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    last_validation_error: str = ""
    for attempt in range(1 + _SCHEMA_RETRY):
        content = _call_llm(body)
        try:
            return _LlmExtraction.model_validate(json.loads(content)).model_dump()
        except (json.JSONDecodeError, ValidationError) as e:
            last_validation_error = str(e)[:200]
            # schema 校验失败重试（_SCHEMA_RETRY 次），耗尽后降级
    raise LlmDegradeSignal("schema_invalid", f"结构化输出校验重试耗尽：{last_validation_error}")


def _call_llm(body: dict) -> str:
    """单次调用（超时/5xx/连接失败 → LlmDegradeSignal，当次降级不重试）。"""
    try:
        resp = httpx.post(settings.nlp.llm_url, json=body, timeout=settings.nlp.llm_timeout_s)
    except httpx.TimeoutException as e:
        raise LlmDegradeSignal("timeout", f"LLM 调用超时（{settings.nlp.llm_timeout_s}s）") from e
    except httpx.HTTPError as e:
        raise LlmDegradeSignal("error", f"LLM 连接失败：{type(e).__name__}: {e}") from e
    if resp.status_code >= 500:
        raise LlmDegradeSignal("error", f"LLM 返回 HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise LlmDegradeSignal("error", f"LLM 返回 HTTP {resp.status_code}：{resp.text[:200]}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LlmDegradeSignal("schema_invalid", f"LLM 响应结构非法：{e}") from e


def _build_prompt(element_catalog: list[ElementCatalogItem]) -> str:
    """prompt 注入要素目录与意图枚举（FR-9.1/9.3）。"""
    catalog = "、".join(f"{item.id}（{item.name}）" for item in element_catalog if item.enabled)
    return (
        "你是影像变化检测系统的任务解析器。将用户自然语言解析为 JSON，字段：\n"
        f'- intent：意图类型，{_INTENTS}；无法判断为 null\n'
        '- location_text：地理范围原文（行政区/地名），无则 null\n'
        f'- elements：要素类别 ID 列表，仅可从目录 [{catalog}] 中选取；无法映射的不要猜测\n'
        '- periods：期次标签列表（如 ["2015","2019"]），无则空列表\n'
        "- confidence：整体置信度 0~1\n"
        "只输出 JSON，不要输出任何其他内容。"
    )
