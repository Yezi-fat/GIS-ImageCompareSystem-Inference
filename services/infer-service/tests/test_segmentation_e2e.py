"""P-012/P-013 验收：伪模型端到端 /infer/segmentation 全管线、
类别超映射 UNSUPPORTED_ELEMENT、/health 真实环境探测。

伪模型语义（scripts/make_fake_models.py）：logit=[128, G, (R+G)/2, mean, R]，
绿色像素→forest(1)、红色像素→building(4)。
"""
import cv2
import numpy as np
import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from cv_common.imaging import decode_b64_png

URL = "http://oss-internal.test/bucket/scene.png?sign=abc"
GEO_EXTENT = [104.01, 30.69, 104.07, 30.73]

# 类别映射对齐伪模型语义与配置 class_ids [0..4]
CLASS_MAPPING = {"forest": 1, "grassland": 2, "snow": 3, "building": 4}
COLORS = {"forest": "#228B22", "grassland": "#9ACD32", "snow": "#F0F8FF", "building": "#CD853F"}


def _scene_png() -> bytes:
    """合成影像：左半绿色（森林）、右上红色方块（建筑）、其余深色背景。512×512。"""
    img = np.full((512, 512, 3), 30, dtype=np.uint8)
    img[:, :256] = (20, 200, 20)        # 森林
    img[:128, 384:] = (220, 60, 40)     # 建筑
    return cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()


def _payload(elements=("forest", "building")) -> dict:
    return {
        "image_url": URL,
        "elements": list(elements),
        "class_mapping": {e: CLASS_MAPPING[e] for e in elements if e in CLASS_MAPPING},
        "colors": {e: COLORS[e] for e in elements if e in COLORS},
        "min_area": 50,
        "geo_extent": GEO_EXTENT,
    }


@pytest.fixture()
def client():
    from infer_service.main import app

    return TestClient(app)


@respx.mock
def test_segmentation_e2e(client):
    """端到端：返回结构符合设计 §4.1；森林/建筑区域统计与合成影像一致。"""
    respx.get(URL).mock(return_value=httpx.Response(200, content=_scene_png()))
    resp = client.post("/infer/segmentation", json=_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # 结构（设计 §4.1 全字段）
    assert set(data) == {
        "combined_mask_png_b64", "per_class_masks", "statistics",
        "geo_transform", "model_info", "actual_provider", "elapsed_ms",
    }
    assert set(data["per_class_masks"]) == {"forest", "building"}
    assert data["actual_provider"] == "local-cpu"
    assert data["model_info"]["name"] == "landcover-seg"
    assert data["model_info"]["version"] == "v0.0-fake"
    assert data["geo_transform"][0] == pytest.approx(GEO_EXTENT[0])

    # 统计正确性：森林 = 左半 512×256 ≈ 131072 px；建筑 = 128×128 = 16384 px
    forest = data["statistics"]["forest"]
    building = data["statistics"]["building"]
    assert abs(forest["area_px"] - 512 * 256) / (512 * 256) < 0.02
    assert abs(building["area_px"] - 128 * 128) / (128 * 128) < 0.02
    assert forest["ratio"] == pytest.approx(0.5, abs=0.01)
    assert forest["patch_count"] == 1 and building["patch_count"] == 1
    assert forest["area_m2"] is not None and forest["area_m2"] > 0

    # 蒙版可解码：森林独立蒙版左半为 255，合成蒙版森林色正确
    forest_mask = decode_b64_png(data["per_class_masks"]["forest"])
    assert forest_mask[256, 100] == 255 and forest_mask[256, 400] == 0
    combined = decode_b64_png(data["combined_mask_png_b64"])  # BGRA
    b, g, r, a = combined[256, 100]
    assert (r, g, b, a) == (0x22, 0x8B, 0x22, 255)


@respx.mock
def test_unsupported_element_not_in_mapping(client):
    """FR-6.6：类别不在映射中 → UNSUPPORTED_ELEMENT（不静默忽略）。"""
    respx.get(URL).mock(return_value=httpx.Response(200, content=_scene_png()))
    resp = client.post("/infer/segmentation", json=_payload(elements=("water",)))
    assert resp.status_code == 400
    assert resp.json()["code"] == "UNSUPPORTED_ELEMENT"


@respx.mock
def test_unsupported_element_out_of_model(client):
    """FR-6.6：映射类别 ID 超出模型输出类别表 → UNSUPPORTED_ELEMENT。"""
    respx.get(URL).mock(return_value=httpx.Response(200, content=_scene_png()))
    payload = _payload()
    payload["elements"] = ["forest"]
    payload["class_mapping"] = {"forest": 99}
    resp = client.post("/infer/segmentation", json=payload)
    assert resp.status_code == 400
    assert resp.json()["code"] == "UNSUPPORTED_ELEMENT"


def test_health_real_capability(client):
    """P-013 验收：无 GPU 环境 gpu_available=false 且不影响启动；字段与设计 §3.5 一致。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["gpu_available"] is False and data["gpu_usable"] is False
    assert data["cpu_cores"] >= 1
    assert set(data["models"]) == {"segmentation", "change_detect"}
    assert data["remote_configured"] is False


@respx.mock
def test_health_reports_loaded_model_after_inference(client):
    """P-013：推理后 /health 上报模型已加载（版本/provider/class_ids）。"""
    respx.get(URL).mock(return_value=httpx.Response(200, content=_scene_png()))
    client.post("/infer/segmentation", json=_payload(elements=("forest",)))
    data = client.get("/health").json()
    seg = data["models"]["segmentation"]
    assert seg["loaded"] is True
    assert seg["version"] == "v0.0-fake" and seg["provider"] == "local-cpu"
    assert seg["class_ids"] == [0, 1, 2, 3, 4]
