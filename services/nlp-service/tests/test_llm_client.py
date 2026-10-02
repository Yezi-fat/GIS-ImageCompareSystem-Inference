"""P-021 验收：llm_client httpx 直调完整端点、结构化输出校验重试 2 次后降级、
超时/5xx/连接失败当次降级（respx 打桩，不依赖真实 LLM）。
"""
import json

import httpx
import pytest
import respx

from cv_common.schemas.nlp import ElementCatalogItem

from nlp_service.config import settings
from nlp_service.nlp.llm_client import LlmDegradeSignal, extract_by_llm

LLM_URL = "http://llm.internal/ai/gw/v1/chat/completions"
CATALOG = [ElementCatalogItem(id="building", name="建筑", model_class_id=4)]


@pytest.fixture(autouse=True)
def _llm_url():
    original = settings.nlp.llm_url
    settings.nlp.llm_url = LLM_URL
    yield
    settings.nlp.llm_url = original


def _llm_response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    })


_GOOD = {"intent": "feature_extraction", "location_text": "成都金牛区",
         "elements": ["building"], "periods": [], "confidence": 0.86}


@respx.mock
def test_llm_success():
    route = respx.post(LLM_URL).mock(return_value=_llm_response(_GOOD))
    result = extract_by_llm("识别金牛区的建筑群", CATALOG)
    assert result["intent"] == "feature_extraction"
    assert result["confidence"] == 0.86
    # 请求体遵循 OpenAI chat completions 协议，prompt 注入要素目录
    sent = json.loads(route.calls[0].request.content)
    assert sent["messages"][1]["content"] == "识别金牛区的建筑群"
    assert "building" in sent["messages"][0]["content"]
    assert route.call_count == 1


@respx.mock
def test_schema_invalid_retry_then_degrade():
    """schema 校验失败重试 2 次（共 3 次），耗尽后抛降级信号 schema_invalid（FR-9.7）。"""
    route = respx.post(LLM_URL).mock(return_value=_llm_response({"bad": "json"}))
    # {"bad": "json"} 通过 schema（全字段可缺省）——需真正非法：content 非 JSON
    route.mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "这不是JSON"}}]
    }))
    with pytest.raises(LlmDegradeSignal) as exc:
        extract_by_llm("识别建筑", CATALOG)
    assert exc.value.reason == "schema_invalid"
    assert route.call_count == 3  # 1 + 2 次重试


@respx.mock
def test_schema_retry_recovers():
    """首次非法、重试后合法 → 正常返回（不当降级）。"""
    route = respx.post(LLM_URL).mock(side_effect=[
        httpx.Response(200, json={"choices": [{"message": {"content": "bad"}}]}),
        _llm_response(_GOOD),
    ])
    result = extract_by_llm("识别建筑", CATALOG)
    assert result["intent"] == "feature_extraction"
    assert route.call_count == 2


@respx.mock
def test_timeout_degrades_immediately():
    """超时当次降级（不重试）。"""
    route = respx.post(LLM_URL).mock(side_effect=httpx.TimeoutException("t"))
    with pytest.raises(LlmDegradeSignal) as exc:
        extract_by_llm("识别建筑", CATALOG)
    assert exc.value.reason == "timeout"
    assert route.call_count == 1


@respx.mock
def test_5xx_degrades_immediately():
    route = respx.post(LLM_URL).mock(return_value=httpx.Response(503, text="down"))
    with pytest.raises(LlmDegradeSignal) as exc:
        extract_by_llm("识别建筑", CATALOG)
    assert exc.value.reason == "error"
    assert route.call_count == 1


def test_llm_not_configured():
    """llm_url 为空 → 降级信号（常驻规则解析，部署期正常形态）。"""
    settings.nlp.llm_url = ""
    with pytest.raises(LlmDegradeSignal) as exc:
        extract_by_llm("识别建筑", CATALOG)
    assert exc.value.reason == "error"
