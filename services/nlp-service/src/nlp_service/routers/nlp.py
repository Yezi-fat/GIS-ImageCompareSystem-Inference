"""/nlp/* 路由（设计 §2.5）：parse、health 两端点。

M4 里程碑：/nlp/parse 接入全量编排（P-024：逐请求降级 + 熔断 + 部分成功）；
/health 上报 nlu.circuit 熔断器状态（FR-9.7，供 Java 监控告警）。
"""
from __future__ import annotations

from fastapi import APIRouter

from cv_common.schemas.health import GeocoderHealth, NlpHealthResponse, NluHealth
from cv_common.schemas.nlp import NlParseRequest, NlParseResponse

from nlp_service.config import settings
from nlp_service.nlp.parser import circuit_state, parse

router = APIRouter()


@router.post("/nlp/parse", response_model=NlParseResponse)
def nlp_parse(req: NlParseRequest) -> NlParseResponse:
    """自然语言任务解析（FR-9，只解析不执行；need_confirm 恒为 true，FR-9.4）。"""
    return parse(req.text, req.element_catalog)


@router.get("/health", response_model=NlpHealthResponse)
def health() -> NlpHealthResponse:
    """NLU / 地理编码可用性 + 熔断器状态上报（FR-9.7）。"""
    return NlpHealthResponse(
        status="ok",
        nlu=NluHealth(
            provider="llm" if settings.nlp.llm_url else "rule-based",
            available=True,
            circuit=circuit_state(),
        ),
        geocoder=GeocoderHealth(
            provider=settings.nlp.geocoder_provider,
            available=bool(settings.nlp.geocoder_url),
        ),
    )
