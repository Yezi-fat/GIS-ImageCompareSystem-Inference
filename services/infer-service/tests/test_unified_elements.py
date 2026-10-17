"""统一要素映射验收（工作任务十二，V1.3）：

- 请求省略 class_mapping → 统一要素映射表（ELEMENTS__MAPPING）按路由结果解析代号；
- 一对多映射：统一要素 → 多个模型类别代号（np.isin 提取）；
- 统一表按模型名键控：低层级路由（sfrs）与默认模型（landcover-seg）各取各的代号；
- 要素不被当前路由模型支持（codes 为空/未配置）→ UNSUPPORTED_ELEMENT；
- GET /infer/elements 暴露统一目录（Java element_catalog / 前端选择器同步数据源）。
"""
import numpy as np
import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from infer_service.config import (
    ElementsSettings,
    ModelClassRef,
    Settings,
    UnifiedElement,
)

IMG_URL = "http://oss-internal.test/unified.png"

# 测试统一映射表（键控伪模型名）：
# veg 一对多（landcover-seg 的 forest+grassland / sfrs 的 trees+grass）；
# water 仅 sfrs 支持（默认模型不支持）
_TEST_MAPPING = [
    {"element": "veg", "name": "植被", "models": {
        "landcover-seg": {"codes": [1, 2], "names": ["forest", "grassland"]},
        "sfrs": {"codes": [1, 2], "names": ["trees", "grass"]},
    }},
    {"element": "water", "name": "水体", "models": {
        "landcover-seg": {"codes": [], "names": []},
        "sfrs": {"codes": [0], "names": ["water"]},
    }},
]


@pytest.fixture(autouse=True)
def _unified_mapping(monkeypatch):
    """注入测试统一映射表（替换默认 DW 表）并重置引擎缓存。"""
    from infer_service.analysis import segmentation as seg_mod
    from infer_service.engines import registry

    parsed = ElementsSettings(mapping=[UnifiedElement(**e) for e in _TEST_MAPPING])
    monkeypatch.setattr(seg_mod.settings, "elements", parsed)
    registry.reset_engines()
    yield
    registry.reset_engines()


def _client() -> TestClient:
    return TestClient(__import__("infer_service.main", fromlist=["app"]).app)


def _post(tile_z=None, elements=("veg",), colors=None):
    """统一口径请求：省略 class_mapping；影像 = 绿块（forest/trees）+ 蓝块（water）。"""
    import cv2

    img = np.full((64, 64, 3), (20, 20, 20), dtype=np.uint8)
    img[:32] = (20, 200, 20)    # 上半绿色 → forest(1)/trees(1)
    img[48:] = (20, 20, 200)    # 底部蓝色 → sfrs water(0)（landcover-seg 无水类）
    png = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()
    respx.get(IMG_URL).mock(return_value=httpx.Response(200, content=png))
    body = {
        "image_url": IMG_URL,
        "elements": list(elements),
        "colors": colors or {"veg": "#228B22", "water": "#1E90FF"},
        "geo_extent": [104.0, 30.0, 104.1, 30.1],
    }
    if tile_z is not None:
        body["tile_z"] = tile_z
    return _client().post("/infer/segmentation", json=body)


@respx.mock
def test_unified_mapping_default_model():
    """省略 class_mapping + 无 tile_z → 统一表按默认模型解析（veg→[1,2]，绿块识别为 veg）。"""
    resp = _post()
    assert resp.status_code == 200
    assert resp.json()["model_info"]["name"] == "landcover-seg"
    assert resp.json()["statistics"]["veg"]["area_px"] > 0


@respx.mock
def test_unified_mapping_low_zoom_model():
    """省略 class_mapping + tile_z≤12 → 统一表按 sfrs 解析（veg→[1,2]，水→[0]）。"""
    resp = _post(tile_z=5, elements=["veg", "water"])
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_info"]["name"] == "sfrs"
    assert data["statistics"]["veg"]["area_px"] > 0
    assert data["statistics"]["water"]["area_px"] > 0  # sfrs 支持水体


@respx.mock
def test_unified_mapping_unsupported_on_routed_model():
    """要素不被当前路由模型支持（codes 空）→ UNSUPPORTED_ELEMENT（FR-6.6 不静默忽略）。"""
    resp = _post(tile_z=15, elements=["water"])  # 默认模型不支持 water
    assert resp.status_code == 400
    assert resp.json()["code"] == "UNSUPPORTED_ELEMENT"
    assert "water" in resp.json()["message"]


@respx.mock
def test_legacy_class_mapping_still_works():
    """兼容旧口径：携带 class_mapping 时仍按其解析（统一表不参与）。"""
    import cv2

    img = np.full((64, 64, 3), (20, 200, 20), dtype=np.uint8)
    png = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()
    respx.get(IMG_URL).mock(return_value=httpx.Response(200, content=png))
    resp = _client().post("/infer/segmentation", json={
        "image_url": IMG_URL, "elements": ["forest"],
        "class_mapping": {"forest": 1}, "colors": {"forest": "#228B22"},
        "geo_extent": [104.0, 30.0, 104.1, 30.1],
    })
    assert resp.status_code == 200
    assert resp.json()["statistics"]["forest"]["area_px"] > 0


def test_elements_endpoint():
    """GET /infer/elements：暴露统一目录（测试注入表）。"""
    resp = _client().get("/infer/elements")
    assert resp.status_code == 200
    data = resp.json()
    by_id = {e["element"]: e for e in data["elements"]}
    assert by_id["veg"]["models"]["landcover-seg"]["codes"] == [1, 2]
    assert by_id["water"]["models"]["sfrs"]["names"] == ["water"]
    assert by_id["water"]["models"]["landcover-seg"]["codes"] == []


def test_default_unified_mapping_table():
    """默认统一表（生产口径）：DW 9 类基准，关键映射断言。"""
    table = {e.element: e for e in Settings().elements.mapping}
    assert len(table) == 9
    assert table["trees"].models["sfrs"].codes == [1]
    assert table["trees"].models["landcover-yolo-v11"].codes == [1]
    assert table["built"].models["landcover-yolo-v11"].codes == [4, 7]  # Industrial+Residential
    assert table["snow_and_ice"].models["landcover-yolo-v11"].codes == []  # EuroSAT 无雪类
    assert table["snow_and_ice"].models["sfrs"].codes == [8]
