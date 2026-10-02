"""P-014/P-015/P-017/P-018 验收：
- P-014 多版本链路：请求携带 model_version 切换（伪模型双版本验证，真实模型交付后收口）；
- P-015 GPU/CPU 双路径：无 GPU 环境 local-gpu 提示 → 回退 CPU 且 actual_provider 如实标记；
- P-017 RemoteEngine：httpx 完整 URL 直调、Tile 批量、批量失败逐块重试一次、超时可控（respx 打桩）；
- P-018 推理信号量：threading.Semaphore 槽位与互斥行为。
"""
import base64
import threading
import time

import numpy as np
import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from infer_service.config import Settings
from infer_service.core import concurrency
from infer_service.engines.model_store import ModelStore
from infer_service.engines.remote import RemoteEngine

ENDPOINT = "http://inference-platform.internal:8000/api/v2/inference"


# ---------- P-014 多版本切换（伪模型 v0.1-fake 复制版） ----------

@pytest.fixture()
def second_fake_version():
    """复制 v0.0-fake 为 v0.1-fake（同内容不同版本号，验证按名+版本懒加载）。"""
    import shutil
    from pathlib import Path

    models_dir = (
        Path(__file__).resolve().parents[3] / "services" / "infer-service" / "models"
    )
    src = models_dir / "landcover-seg" / "v0.0-fake"
    dst = models_dir / "landcover-seg" / "v0.1-fake"
    if not dst.exists():
        shutil.copytree(src, dst)
    yield "v0.1-fake"
    shutil.rmtree(dst, ignore_errors=True)


@respx.mock
def test_model_version_switch(second_fake_version):
    """P-014/FR-3.5：请求切换 model_version 后按新版本推理（model_info 如实返回）。"""
    import cv2

    from infer_service.engines import registry

    registry.reset_engines()
    img = np.full((64, 64, 3), (20, 200, 20), dtype=np.uint8)
    png = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()
    url = "http://oss-internal.test/x.png"
    respx.get(url).mock(return_value=httpx.Response(200, content=png))

    client = TestClient(__import__("infer_service.main", fromlist=["app"]).app)
    base = {
        "image_url": url,
        "elements": ["forest"],
        "class_mapping": {"forest": 1},
        "colors": {"forest": "#228B22"},
        "geo_extent": [104.0, 30.0, 104.1, 30.1],
    }
    resp_default = client.post("/infer/segmentation", json=base)
    assert resp_default.json()["model_info"]["version"] == "v0.0-fake"

    resp_v1 = client.post("/infer/segmentation", json={**base, "model_version": second_fake_version})
    assert resp_v1.status_code == 200
    assert resp_v1.json()["model_info"]["version"] == "v0.1-fake"
    registry.reset_engines()


# ---------- P-015 GPU/CPU 双路径与回退标记 ----------

@respx.mock
def test_gpu_hint_fallback_to_cpu():
    """无 GPU 环境：X-Inference-Provider: local-gpu → 回退 CPU 且 actual_provider 如实标记。"""
    import cv2

    from infer_service.engines import registry

    registry.reset_engines()
    img = np.full((64, 64, 3), (20, 200, 20), dtype=np.uint8)
    png = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()
    url = "http://oss-internal.test/y.png"
    respx.get(url).mock(return_value=httpx.Response(200, content=png))

    client = TestClient(__import__("infer_service.main", fromlist=["app"]).app)
    resp = client.post(
        "/infer/segmentation",
        json={"image_url": url, "elements": ["forest"], "class_mapping": {"forest": 1},
              "colors": {"forest": "#228B22"}, "geo_extent": [104.0, 30.0, 104.1, 30.1]},
        headers={"X-Inference-Provider": "local-gpu"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["actual_provider"] == "local-cpu"   # 回退如实标记（FR-3.2/5.2）
    assert data["model_info"]["provider"] == "local-cpu"
    registry.reset_engines()


# ---------- P-017 RemoteEngine ----------

def _prob_item(c=5, h=64, w=64, value=0.2):
    arr = np.full((c, h, w), value, dtype=np.float32)
    return {"shape": [c, h, w], "data_b64": base64.b64encode(arr.tobytes()).decode()}


def _remote_engine() -> RemoteEngine:
    settings = Settings()
    settings.remote.endpoint = ENDPOINT
    return RemoteEngine(settings)


@respx.mock
def test_remote_batch_grouping():
    """FR-1.9：9 个 Tile 按 batch_size=8 分 2 批提交。"""
    import json

    engine = _remote_engine()

    def _handler(req: httpx.Request) -> httpx.Response:
        n = len(json.loads(req.content)["tiles"])
        return httpx.Response(200, json={"probs": [_prob_item() for _ in range(n)]})

    route = respx.post(ENDPOINT).mock(side_effect=_handler)
    tiles = [np.zeros((3, 64, 64), dtype=np.float32) for _ in range(9)]
    probs = engine.segment(tiles)
    assert len(probs) == 9 and probs[0].shape == (5, 64, 64)
    assert route.call_count == 2  # 8 + 1


@respx.mock
def test_remote_batch_failure_fallback_single():
    """批量失败自动降级为逐块重试一次（FR-1.9/设计 §3.1.3）。"""
    engine = _remote_engine()

    def _handler(req: httpx.Request) -> httpx.Response:
        import json

        n = len(json.loads(req.content)["tiles"])
        if n > 1:
            return httpx.Response(500, text="batch error")
        return httpx.Response(200, json={"probs": [_prob_item()]})

    route = respx.post(ENDPOINT).mock(side_effect=_handler)
    tiles = [np.zeros((3, 64, 64), dtype=np.float32) for _ in range(3)]
    probs = engine.segment(tiles)
    assert len(probs) == 3
    assert route.call_count == 1 + 3  # 1 次批量失败 + 3 次逐块重试


@respx.mock
def test_remote_timeout():
    """超时可控（25s 配置，单次调用超时成形于本层）。"""
    engine = _remote_engine()
    respx.post(ENDPOINT).mock(side_effect=httpx.TimeoutException("timeout"))
    with pytest.raises(Exception) as exc:
        engine.segment([np.zeros((3, 64, 64), dtype=np.float32)])
    from cv_common.errors import InferenceFailedError

    assert isinstance(exc.value, InferenceFailedError)
    assert "超时" in exc.value.message


def test_remote_requires_endpoint():
    """endpoint 为空（未配置远程，正常形态）→ 明确报错而非隐式失败。"""
    from cv_common.errors import InferenceFailedError

    with pytest.raises(InferenceFailedError):
        RemoteEngine(Settings())


# ---------- P-018 推理信号量 ----------

def test_semaphore_slots_and_mutual_exclusion():
    """threading.Semaphore：CPU 2 槽 / GPU 4 槽；互斥行为可断言。"""
    concurrency.reset_semaphores()
    cpu_sem = concurrency.get_inference_semaphore("local-cpu")
    gpu_sem = concurrency.get_inference_semaphore("local-gpu")
    assert cpu_sem is concurrency.get_inference_semaphore("local-cpu")  # 单例
    assert cpu_sem._value == 2 and gpu_sem._value == 4

    # 互斥：2 槽占满后第三方排队
    acquired_order = []

    def worker(tag: str):
        with cpu_sem:
            acquired_order.append(("enter", tag))
            time.sleep(0.05)
            acquired_order.append(("exit", tag))

    cpu_sem.acquire()  # 占 1 槽
    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    time.sleep(0.02)
    in_flight = [e for e in acquired_order if e[0] == "enter"]
    done = [e for e in acquired_order if e[0] == "exit"]
    assert len(in_flight) - len(done) <= 1  # 仅剩 1 槽可入
    cpu_sem.release()
    for t in threads:
        t.join()
    assert len([e for e in acquired_order if e[0] == "exit"]) == 3
    concurrency.reset_semaphores()
