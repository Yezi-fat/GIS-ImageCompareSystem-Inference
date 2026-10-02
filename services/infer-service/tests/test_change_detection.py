"""P-016 + P-018a 验收：/infer/change-detection 全链路（伪模型）、
配准校验告警路径（ALIGNMENT_FAILED）、estimated_shift_px/auto_aligned 响应字段。

伪变化模型语义（scripts/make_fake_models.py）：prob = |mean(after)-mean(before)|
（0~1 输入下即双时相亮度差）。
"""
import cv2
import numpy as np
import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from cv_common.imaging import decode_b64_png

BEFORE_URL = "http://oss-internal.test/bucket/before.png?sign=a"
AFTER_URL = "http://oss-internal.test/bucket/after.png?sign=b"
GEO_EXTENT = [104.01, 30.69, 104.07, 30.73]


def _scene(change: bool = True, shift_x: int = 0) -> tuple[bytes, bytes]:
    """双时相合成影像：固定种子的随机噪声纹理（相位相关需要非周期纹理，
    周期条纹会产生混叠伪峰）；after 在 [200:400, 200:400] 增亮（变化区）。
    shift_x：after 整体右移像素数（配准校验测试用）。"""
    rng = np.random.default_rng(42)
    before = rng.integers(20, 60, (512, 512, 3)).astype(np.uint8)
    after = before.copy()
    if change:
        after[200:400, 200:400] = 220
    if shift_x:
        after = np.roll(after, shift_x, axis=1)
    enc = lambda img: cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()
    return enc(before), enc(after)


def _payload(**over) -> dict:
    payload = {
        "before_url": BEFORE_URL,
        "after_url": AFTER_URL,
        "geo_extent": GEO_EXTENT,
        "threshold": 0.5,
        "min_area": 50,
    }
    payload.update(over)
    return payload


@pytest.fixture()
def client():
    from infer_service.main import app

    return TestClient(app)


@respx.mock
def test_change_detection_e2e(client):
    """P-016：返回 mask + probmap + 统计，符合设计 §4.2；变化区域定位正确。"""
    before_png, after_png = _scene()
    respx.get(BEFORE_URL).mock(return_value=httpx.Response(200, content=before_png))
    respx.get(AFTER_URL).mock(return_value=httpx.Response(200, content=after_png))

    resp = client.post("/infer/change-detection", json=_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert set(data) >= {
        "mask_png_b64", "probmap_png_b64", "statistics",
        "estimated_shift_px", "auto_aligned", "geo_transform",
        "model_info", "actual_provider", "elapsed_ms",
    }
    assert data["actual_provider"] == "local-cpu"
    assert data["model_info"]["name"] == "change-detection"
    assert data["auto_aligned"] is False           # 自动配准列 M6（评审 D-03）
    assert data["estimated_shift_px"] <= 4         # 同源图无平移

    # 变化区定位：mask 在 [200:400]² 为 255，其余为 0
    mask = decode_b64_png(data["mask_png_b64"])
    assert mask[300, 300] == 255 and mask[50, 50] == 0
    stats = data["statistics"]
    assert stats["change_count"] == 1
    assert abs(stats["total_area_px"] - 200 * 200) / (200 * 200) < 0.05
    assert stats["total_area_m2"] > 0

    # 概率图：变化区高概率，背景低概率
    probmap = decode_b64_png(data["probmap_png_b64"])
    assert probmap[300, 300] > 128 and probmap[50, 50] < 10


@respx.mock
def test_change_detection_no_change(client):
    """双图相同 → 无变化区域。"""
    before_png, _ = _scene(change=False)
    respx.get(BEFORE_URL).mock(return_value=httpx.Response(200, content=before_png))
    respx.get(AFTER_URL).mock(return_value=httpx.Response(200, content=before_png))
    resp = client.post("/infer/change-detection", json=_payload())
    assert resp.status_code == 200
    assert resp.json()["statistics"]["change_count"] == 0


@respx.mock
def test_alignment_within_threshold(client):
    """P-018a：平移 ≤4px（阈值内）→ 正常出结果且响应含 estimated_shift_px。"""
    before_png, after_png = _scene(shift_x=2)
    respx.get(BEFORE_URL).mock(return_value=httpx.Response(200, content=before_png))
    respx.get(AFTER_URL).mock(return_value=httpx.Response(200, content=after_png))
    resp = client.post("/infer/change-detection", json=_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["estimated_shift_px"] == pytest.approx(2.0, abs=1.0)
    assert data["auto_aligned"] is False


@respx.mock
def test_alignment_over_threshold_422(client):
    """P-018a：平移超阈值（默认 4px）→ ALIGNMENT_FAILED(422)，message 注明自动配准未启用。"""
    before_png, after_png = _scene(shift_x=30)
    respx.get(BEFORE_URL).mock(return_value=httpx.Response(200, content=before_png))
    respx.get(AFTER_URL).mock(return_value=httpx.Response(200, content=after_png))
    resp = client.post("/infer/change-detection", json=_payload(auto_align=True))
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "ALIGNMENT_FAILED"
    assert "自动配准能力本期未启用" in body["message"]  # 不静默忽略（§3.2.5）
