"""P-022 验收（评审 T-02 口径）：三适配器协议正确性、多候选解析、置信度回填
——全部以 respx fixture 打桩；真实地理编码服务联调为可选验收项。
"""
import httpx
import pytest
import respx

from nlp_service.config import settings
from nlp_service.nlp.geocoder import GeocodeError, geocode

GEOCODER_URL = "http://geocoder.internal/geocode"

NOMINATIM_FIXTURE = [
    {"display_name": "金牛区, 成都市, 四川省", "boundingbox": ["30.65", "30.78", "103.98", "104.13"],
     "importance": 0.86},
    {"display_name": "金牛区（另一候选）", "boundingbox": ["30.60", "30.70", "103.90", "104.00"],
     "importance": 0.40},
]
AMAP_FIXTURE = {"status": "1", "geocodes": [
    {"formatted_address": "四川省成都市金牛区", "rectangle": "103.98,30.65;104.13,30.78"},
]}
BAIDU_FIXTURE = {"status": 0, "result": {"location": {"lng": 104.05, "lat": 30.69}, "confidence": 80}}


@pytest.fixture(autouse=True)
def _geocoder_url():
    original_url, original_provider = settings.nlp.geocoder_url, settings.nlp.geocoder_provider
    settings.nlp.geocoder_url = GEOCODER_URL
    yield
    settings.nlp.geocoder_url, settings.nlp.geocoder_provider = original_url, original_provider


@respx.mock
def test_nominatim_bbox_and_candidates():
    """Nominatim：boundingbox [south,north,west,east] → [minx,miny,maxx,maxy]；多候选返回。"""
    settings.nlp.geocoder_provider = "nominatim"
    respx.get(GEOCODER_URL).mock(return_value=httpx.Response(200, json=NOMINATIM_FIXTURE))
    result = geocode("成都金牛区")
    assert result["bbox"] == [103.98, 30.65, 104.13, 30.78]
    assert result["confidence"] == 0.86
    assert len(result["candidates"]) == 1  # 歧义候选交由用户选择（FR-9.2）


@respx.mock
def test_amap_protocol():
    """高德：rectangle "minx,miny;maxx,maxy" 解析。"""
    settings.nlp.geocoder_provider = "amap"
    route = respx.get(GEOCODER_URL).mock(return_value=httpx.Response(200, json=AMAP_FIXTURE))
    result = geocode("金牛区")
    assert result["bbox"] == [103.98, 30.65, 104.13, 30.78]
    assert "key=" in str(route.calls[0].request.url) or "key" in str(route.calls[0].request.url.params)


@respx.mock
def test_baidu_protocol():
    """百度：点坐标外扩 bbox，置信度下调（精度受限口径）。"""
    settings.nlp.geocoder_provider = "baidu"
    respx.get(GEOCODER_URL).mock(return_value=httpx.Response(200, json=BAIDU_FIXTURE))
    result = geocode("金牛区")
    assert result["bbox"][0] == pytest.approx(104.05 - 0.05)
    assert result["confidence"] <= 0.6


@respx.mock
def test_geocode_failure_raises_for_partial_success():
    """服务 5xx → GeocodeError（由 parser 部分成功降级，FR-9.8）。"""
    settings.nlp.geocoder_provider = "nominatim"
    respx.get(GEOCODER_URL).mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(GeocodeError):
        geocode("金牛区")


def test_geocoder_not_configured():
    settings.nlp.geocoder_url = ""
    with pytest.raises(GeocodeError):
        geocode("金牛区")
