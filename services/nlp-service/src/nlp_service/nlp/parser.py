"""NLU 编排（设计 §3.4.1，FR-9.1，P-024 实现）：逐请求降级 + 熔断 + 部分成功。

/nlp/parse(text, element_catalog)
  ① NLU 抽取（逐请求降级，FR-9.7）：
     熔断器断流（circuit.allow()=False）→ 直接用 rule_based
     否则 try llm_client：schema 校验失败重试 2 次；
       超时(llm_timeout_s)/5xx/连接失败/重试耗尽 → 当次落 rule_based，
       响应标记 nlu_provider=rule-based，
       记 structlog WARN：event=nlu_degraded, reason=timeout|error|schema_invalid
  ② 要素映射：elements 对照 element_catalog（别名/修饰语归一，词表内置，评审 D-02）；
     无法映射 → unsupported 列表（FR-9.3）
  ③ 地理编码（FR-9.8 部分成功）：失败不报错 → location=null +
     uncertain_fields 含 "location" + need_confirm=true；
     仅当意图缺失且位置缺失 → LocationUnresolvedError（LOCATION_UNRESOLVED/422）
  ④ 组装响应：confidence < 0.7 的字段在 uncertain_fields 中标注（FR-9.4）
"""
from __future__ import annotations

import structlog

from cv_common.errors import LocationUnresolvedError, NluUnavailableError
from cv_common.schemas.nlp import (
    ElementCatalogItem,
    LocationCandidate,
    NlLocation,
    NlParseResponse,
    UnsupportedItem,
)

from nlp_service.config import settings
from nlp_service.nlp import gazetteer, rule_based
from nlp_service.nlp.circuit import CircuitBreaker
from nlp_service.nlp.geocoder import GeocodeError, geocode
from nlp_service.nlp.llm_client import LlmDegradeSignal, extract_by_llm

logger = structlog.get_logger(__name__)

# 进程级熔断器（FR-9.7；/health 经 circuit_state() 上报）
_circuit = CircuitBreaker(
    failure_threshold=settings.nlp.circuit.failure_threshold,
    window_s=settings.nlp.circuit.window_s,
    open_duration_s=settings.nlp.circuit.open_duration_s,
)

_INTENT_VALUES = {"feature_extraction", "feature_comparison", "temporal_analysis"}


def parse(text: str, element_catalog: list[ElementCatalogItem]) -> NlParseResponse:
    """自然语言任务解析编排（流程见模块 docstring）。"""
    _check_availability()

    # ① NLU 抽取（逐请求降级）
    extraction, nlu_provider = _extract(text, element_catalog)

    # ② 要素映射（别名/修饰语归一，FR-9.3）
    elements, unsupported = _map_elements(extraction.get("elements") or [], element_catalog)

    # ③ 地理编码（部分成功降级，FR-9.8）
    location, uncertain_fields = _resolve_location(extraction.get("location_text"))

    # ④ 置信度与不确定字段标注（FR-9.4）；意图/位置均缺失 → 完全不可执行
    intent = extraction.get("intent")
    if intent not in _INTENT_VALUES:
        intent = None
    confidence = float(extraction.get("confidence", 0.5))
    if intent is None and location is None:
        raise LocationUnresolvedError(
            f"无法从输入解析出有效意图与地理范围：{text!r}——结果完全不可执行，"
            f"请补充要素/位置描述后重试（FR-9.8）"
        )
    if intent is None:
        uncertain_fields.append("intent")
    if confidence < 0.7 and "intent" not in uncertain_fields:
        uncertain_fields.append("intent")  # 低置信度强制确认高亮（FR-9.4）

    return NlParseResponse(
        intent=intent,
        location=location,
        elements=elements,
        periods=extraction.get("periods") or [],
        confidence=confidence,
        need_confirm=True,  # 恒为 true（FR-9.4 强制用户确认）
        unsupported=unsupported,
        nlu_provider=nlu_provider,
        uncertain_fields=uncertain_fields or None,
    )


def circuit_state() -> str:
    """熔断器状态（/health 上报，FR-9.7）。"""
    return _circuit.state()


def _check_availability() -> None:
    """部署期能力检查：LLM 未配置（规则解析兜底，可用）；
    仅当 LLM、地理编码、地名词典均不可用时整体裁剪（NLU_UNAVAILABLE，需求 6.9）。"""
    if settings.nlp.llm_url:
        return
    if settings.nlp.geocoder_url or gazetteer.load_gazetteer():
        return  # 规则解析 + 词典/地理编码仍可用（能力受限，nlu_provider 如实标记）
    raise NluUnavailableError(
        "NLU 与地理编码均未配置且地名词典为空——自然语言解析能力不可用（FR-9 裁剪场景）"
    )


def _extract(text: str, element_catalog: list[ElementCatalogItem]) -> tuple[dict, str]:
    """NLU 抽取：熔断断流或 LLM 未配置 → 规则；LLM 故障 → 当次降级规则（FR-9.7）。"""
    gaz = gazetteer.load_gazetteer()
    if not settings.nlp.llm_url:
        return rule_based.extract_by_rules(text, gaz), "rule-based"
    if not _circuit.allow():
        logger.warning("nlu_degraded", reason="circuit_open")
        return rule_based.extract_by_rules(text, gaz), "rule-based"
    try:
        result = extract_by_llm(text, element_catalog)
        _circuit.on_success()
        return result, "llm"
    except LlmDegradeSignal as signal:
        _circuit.on_failure()
        logger.warning("nlu_degraded", reason=signal.reason, detail=str(signal)[:200])
        return rule_based.extract_by_rules(text, gaz), "rule-based"


def _map_elements(
    raw_elements: list[str], element_catalog: list[ElementCatalogItem]
) -> tuple[list[str], list[UnsupportedItem]]:
    """要素映射：id/name/别名归一（FR-9.3）；无法映射 → unsupported + 可识别类别。"""
    by_id = {item.id: item for item in element_catalog if item.enabled}
    by_name = {item.name: item for item in element_catalog if item.enabled}
    available = sorted(by_id)
    elements: list[str] = []
    unsupported: list[UnsupportedItem] = []
    for raw in raw_elements:
        normalized = rule_based.ELEMENT_ALIASES.get(raw, raw)
        item = by_id.get(normalized) or by_name.get(normalized)
        if item:
            if item.id not in elements:
                elements.append(item.id)
        else:
            unsupported.append(UnsupportedItem(
                raw=raw,
                reason=f"该要素暂不支持（可识别类别：{'、'.join(available)}）",
            ))
    return elements, unsupported


def _resolve_location(location_text: str | None) -> tuple[NlLocation | None, list[str]]:
    """地理编码（FR-9.8 部分成功）。

    链路：地理编码服务 → 失败/未配置时回退地名词典（规则降级链路的离线定位能力，
    FR-9.6）→ 词典命中则低置信度返回并标注 uncertain（交用户确认）；
    均未命中 → location=null + uncertain_fields 含 location。
    """
    if not location_text:
        return None, []
    try:
        geo = geocode(location_text)
    except GeocodeError as e:
        logger.warning("geocode_failed", location=location_text, reason=str(e)[:200])
        entry = gazetteer.load_gazetteer().get(location_text)
        if entry:
            return (
                NlLocation(raw=location_text, bbox=entry["bbox"], confidence=0.5, candidates=[]),
                ["location"],  # 词典降级来源，需用户确认（FR-9.4）
            )
        return None, ["location"]
    return (
        NlLocation(
            raw=location_text,
            bbox=geo["bbox"],
            confidence=geo["confidence"],
            candidates=[LocationCandidate(**c) for c in geo.get("candidates", [])],
        ),
        [],
    )
