"""P-024 验收：/nlp/parse 编排全链路——逐请求降级、熔断、地理编码部分成功、
unsupported 标注、LOCATION_UNRESOLVED、/health 熔断状态。

LLM 与地理编码均 respx 打桩；熔断器每测试重置（不依赖真实外部服务）。
"""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from nlp_service.config import settings
from nlp_service.nlp.circuit import CircuitBreaker

LLM_URL = "http://llm.internal/ai/gw/v1/chat/completions"
GEOCODER_URL = "http://geocoder.internal/geocode"
CATALOG = [{"id": "forest", "name": "森林", "model_class_id": 1},
           {"id": "building", "name": "建筑", "model_class_id": 4}]

NOMINATIM_FIXTURE = [
    {"display_name": "金牛区, 成都市, 四川省", "boundingbox": ["30.65", "30.78", "103.98", "104.13"],
     "importance": 0.86},
]


@pytest.fixture()
def client():
    from nlp_service.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_state():
    """每测试重置：配置还原 + 熔断器闭合。"""
    import nlp_service.nlp.parser as parser

    originals = (settings.nlp.llm_url, settings.nlp.geocoder_url, settings.nlp.geocoder_provider)
    parser._circuit = CircuitBreaker()
    yield
    settings.nlp.llm_url, settings.nlp.geocoder_url, settings.nlp.geocoder_provider = originals
    parser._circuit = CircuitBreaker()


def _post(client, text):
    return client.post("/nlp/parse", json={"text": text, "element_catalog": CATALOG})


def _llm_ok(content: dict) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]
    })


# ---------- 规则降级主链路（LLM 未配置） ----------

def test_rule_based_full_path(client):
    """验收例句（需求验收 9）：识别四川成都金牛区xx街道的建筑群。
    LLM 未配置 + 地理编码未配置 → 规则解析 + 地名词典定位（离线降级链）。"""
    settings.nlp.llm_url = ""
    settings.nlp.geocoder_url = ""
    resp = _post(client, "识别四川成都金牛区xx街道的建筑群")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["intent"] == "feature_extraction"
    assert data["elements"] == ["building"]           # 别名归一：建筑群→建筑→building
    assert data["nlu_provider"] == "rule-based"
    assert data["need_confirm"] is True               # FR-9.4 恒需确认
    assert data["location"]["bbox"] == [103.98, 30.65, 104.13, 30.78]  # 词典定位
    assert "location" in data["uncertain_fields"]     # 词典降级来源标注


def test_unsupported_element(client):
    """FR-9.3：无法映射的要素明确告知 + 列出可识别类别，不静默忽略。"""
    settings.nlp.llm_url = ""
    resp = _post(client, "识别金牛区的水稻田")
    data = resp.json()
    assert data["elements"] == []
    assert data["unsupported"][0]["raw"] == "水稻田"
    assert "forest" in data["unsupported"][0]["reason"]


def test_location_unresolved_422(client):
    """意图与位置均缺失（完全不可执行）→ LOCATION_UNRESOLVED（FR-9.8/D-04）。"""
    settings.nlp.llm_url = ""
    resp = _post(client, "随便看看")
    assert resp.status_code == 422
    assert resp.json()["code"] == "LOCATION_UNRESOLVED"


# ---------- LLM 链路 + 逐请求降级（FR-9.7） ----------

@respx.mock
def test_llm_path(client):
    """LLM 正常 + Nominatim 正常 → nlu_provider=llm，地理编码置信度透传。"""
    settings.nlp.llm_url = LLM_URL
    settings.nlp.geocoder_url = GEOCODER_URL
    settings.nlp.geocoder_provider = "nominatim"
    respx.post(LLM_URL).mock(return_value=_llm_ok(
        {"intent": "feature_extraction", "location_text": "金牛区",
         "elements": ["building"], "periods": [], "confidence": 0.86}))
    respx.get(GEOCODER_URL).mock(return_value=httpx.Response(200, json=NOMINATIM_FIXTURE))
    resp = _post(client, "识别金牛区的建筑群")
    data = resp.json()
    assert data["nlu_provider"] == "llm"
    assert data["location"]["bbox"] == [103.98, 30.65, 104.13, 30.78]
    assert data["uncertain_fields"] is None           # 高置信度无标注


@respx.mock
def test_llm_failure_degrades_per_request(client, capsys):
    """LLM 故障注入：当次降级成功且 nlu_provider=rule-based、日志含 nlu_degraded。"""
    settings.nlp.llm_url = LLM_URL
    settings.nlp.geocoder_url = ""
    respx.post(LLM_URL).mock(return_value=httpx.Response(503, text="down"))
    resp = _post(client, "识别金牛区的建筑")
    assert resp.status_code == 200                    # 不因 LLM 抖动整体失败
    data = resp.json()
    assert data["nlu_provider"] == "rule-based"
    assert data["intent"] == "feature_extraction"
    assert "nlu_degraded" in capsys.readouterr().out  # 结构化降级日志


@respx.mock
def test_circuit_opens_and_health_reports(client):
    """连续 5 次 LLM 故障 → 断流：第 6 次请求不再调用 LLM；/health 上报 circuit=open。"""
    settings.nlp.llm_url = LLM_URL
    settings.nlp.geocoder_url = ""
    route = respx.post(LLM_URL).mock(return_value=httpx.Response(503, text="down"))
    for _ in range(5):
        assert _post(client, "识别金牛区的建筑").json()["nlu_provider"] == "rule-based"
    assert route.call_count == 5
    # 断流后第 6 次：直接走规则，不再调 LLM
    _post(client, "识别金牛区的建筑")
    assert route.call_count == 5
    health = client.get("/health").json()
    assert health["nlu"]["circuit"] == "open"


@respx.mock
def test_geocode_failure_partial_success(client):
    """FR-9.8：NLU 成功而地理编码失败（服务 502 且词典未命中）→ 200 部分成功。"""
    settings.nlp.llm_url = LLM_URL
    settings.nlp.geocoder_url = GEOCODER_URL
    settings.nlp.geocoder_provider = "nominatim"
    respx.post(LLM_URL).mock(return_value=_llm_ok(
        {"intent": "feature_extraction", "location_text": "不存在的地方",
         "elements": ["forest"], "periods": [], "confidence": 0.9}))
    respx.get(GEOCODER_URL).mock(return_value=httpx.Response(502, text="bad gateway"))
    resp = _post(client, "识别某个地方的森林")
    assert resp.status_code == 200                    # 部分成功而非错误
    data = resp.json()
    assert data["location"] is None
    assert "location" in data["uncertain_fields"]
    assert data["need_confirm"] is True
