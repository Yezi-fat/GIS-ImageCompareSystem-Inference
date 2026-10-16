"""层级路由验收（工作任务十一，V1.2）：

- tile_z ≤ 阈值（默认 12）→ 低层级模型（sfrs/SegFormer-RS 伪模型，DW 9 类）；
- tile_z > 阈值 / 未带 tile_z → 默认模型（landcover-seg 伪模型）；
- 显式 model_name/model_version 不参与路由；
- 低层级类别映射：请求 low_zoom_class_mapping 优先，L1 配置兜底，要素缺失 → UNSUPPORTED_ELEMENT；
- 预处理归一化：mean/std 参数数学正确性。
"""
import numpy as np
import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from infer_service.config import Settings
from infer_service.engines import registry
from infer_service.pipeline.preprocess import preprocess_tile

IMG_URL = "http://oss-internal.test/routing.png"


def _client() -> TestClient:
    return TestClient(__import__("infer_service.main", fromlist=["app"]).app)


def _post(tile_z: int | None, **overrides):
    """发起分割请求（绿色影像：默认模型→forest(1)，sfrs→trees(1)，二者类别 ID 巧合相同）。"""
    import cv2

    img = np.full((64, 64, 3), (20, 200, 20), dtype=np.uint8)
    png = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()
    respx.get(IMG_URL).mock(return_value=httpx.Response(200, content=png))
    body = {
        "image_url": IMG_URL,
        "elements": ["forest"],
        "class_mapping": {"forest": 1},
        "colors": {"forest": "#228B22"},
        "geo_extent": [104.0, 30.0, 104.1, 30.1],
    }
    if tile_z is not None:
        body["tile_z"] = tile_z
    body.update(overrides)
    return _client().post("/infer/segmentation", json=body)


@pytest.fixture(autouse=True)
def _reset():
    registry.reset_engines()
    yield
    registry.reset_engines()


# ---------- 路由判定矩阵 ----------

@respx.mock
def test_low_zoom_routed_to_sfrs():
    """z=5 ≤ 12 → sfrs 模型；class_ids 自 ONNX names 推导 [0..8]（DW 9 类）。"""
    resp = _post(tile_z=5)
    assert resp.status_code == 200
    info = resp.json()["model_info"]
    assert info["name"] == "sfrs"
    assert info["version"] == "v0.0-fake"
    assert info["class_ids"] == list(range(9))
    assert resp.json()["statistics"]["forest"]["area_px"] > 0  # 绿→trees


@respx.mock
def test_boundary_threshold_inclusive():
    """边界：z=12（=阈值）→ sfrs；z=13 → 默认模型。"""
    assert _post(tile_z=12).json()["model_info"]["name"] == "sfrs"
    assert _post(tile_z=13).json()["model_info"]["name"] == "landcover-seg"


@respx.mock
def test_no_tile_z_no_routing():
    """未带 tile_z → 不路由，恒用默认模型（向后兼容）。"""
    resp = _post(tile_z=None)
    assert resp.json()["model_info"]["name"] == "landcover-seg"


@respx.mock
def test_explicit_model_bypasses_routing():
    """显式 model_name/model_version 不参与路由（FR-3.5 优先）。"""
    resp = _post(tile_z=5, model_name="landcover-seg")
    assert resp.json()["model_info"]["name"] == "landcover-seg"
    resp = _post(tile_z=5, model_version="v0.0-fake")
    assert resp.json()["model_info"]["name"] == "landcover-seg"


# ---------- 低层级类别映射 ----------

@respx.mock
def test_low_zoom_mapping_from_config_fallback():
    """未下发 low_zoom_class_mapping → L1 配置兜底（默认 forest→1=trees）。"""
    resp = _post(tile_z=5)
    assert resp.status_code == 200
    assert resp.json()["statistics"]["forest"]["area_px"] > 0


@respx.mock
def test_low_zoom_mapping_from_request():
    """请求 low_zoom_class_mapping 优先于配置（forest→6=built：纯绿影像无 built，面积为 0）。"""
    resp = _post(tile_z=5, low_zoom_class_mapping={"forest": 6})
    assert resp.status_code == 200
    assert resp.json()["statistics"]["forest"]["area_px"] == 0  # 绿→trees≠6


@respx.mock
def test_low_zoom_mapping_missing_element():
    """低层级映射缺要素 → UNSUPPORTED_ELEMENT（不静默忽略，FR-6.6）。"""
    resp = _post(tile_z=5, elements=["snow"], class_mapping={"snow": 3},
                 colors={"snow": "#F0F8FF"}, low_zoom_class_mapping={"forest": 1})
    assert resp.status_code == 400
    assert resp.json()["code"] == "UNSUPPORTED_ELEMENT"
    assert "low_zoom_class_mapping" in resp.json()["message"]


# ---------- 预处理归一化 ----------

def test_preprocess_default_255_only():
    """缺省：仅 /255 归一化。"""
    tile = np.full((4, 4, 3), 255, dtype=np.uint8)
    out = preprocess_tile(tile)
    assert out.shape == (3, 4, 4) and out.dtype == np.float32
    assert np.allclose(out, 1.0)


def test_preprocess_mean_std():
    """mean/std：(x/255 - mean) / std（RGB 通道维广播）。"""
    tile = np.zeros((2, 2, 3), dtype=np.uint8)
    out = preprocess_tile(tile, mean=[0.5, 0.0, 0.0], std=[0.5, 1.0, 1.0])
    assert np.allclose(out[0], -1.0)  # (0-0.5)/0.5
    assert np.allclose(out[1], 0.0) and np.allclose(out[2], 0.0)
    tile = np.full((2, 2, 3), 255, dtype=np.uint8)
    out = preprocess_tile(tile, mean=[0.5, 0.0, 0.0], std=[0.5, 1.0, 1.0])
    assert np.allclose(out[0], 1.0)   # (1-0.5)/0.5
    assert np.allclose(out[1], 1.0) and np.allclose(out[2], 1.0)


def test_routing_config_defaults():
    """路由配置：阈值默认 12，低层级模型 sfrs，DW 兜底映射。"""
    routing = Settings().models.routing
    assert routing.zoom_threshold == 12
    assert routing.low_zoom_name == "sfrs"
    assert routing.low_zoom_class_mapping["forest"] == 1
    assert routing.low_zoom_class_mapping["snow"] == 8
