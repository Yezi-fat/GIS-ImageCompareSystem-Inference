"""bug-2026-09-28 解决方案验收（v1.0.0）：

- Q1-P1：SERVICE__DEFAULT_PROVIDER 非法取值（如 local_gpu 下划线形式）启动即拒绝；
- Q1-P2：X-Inference-Provider 提示头非法取值 → 400 INVALID_INPUT（不再静默兜底）；
- Q1-P3：local-gpu 提示因 GPU 不可用兜底 local-cpu 时记 WARN 日志；
- Q2-P1/Q3-P1：GET /infer/models 模型发现（双产物/类别表/加载状态/默认版本）；
- Q2-P2：class_ids 缺省时从 ONNX names 自动推导，显式配置优先。
"""
import numpy as np
import pytest
import respx
import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from infer_service.config import Settings
from infer_service.engines import registry
from infer_service.engines.model_store import ModelStore


def _client() -> TestClient:
    return TestClient(__import__("infer_service.main", fromlist=["app"]).app)


# ---------- Q1-P1：default_provider 启动校验 ----------

def test_invalid_default_provider_rejected_at_startup(monkeypatch):
    """Q1-P1：local_gpu（下划线形式）等非法取值启动即拒绝，报出合法值清单。"""
    monkeypatch.setenv("SERVICE__DEFAULT_PROVIDER", "local_gpu")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "local-gpu" in str(exc.value)  # 错误信息含合法值（连字符形式）


def test_valid_default_provider_accepted(monkeypatch):
    """合法三值均可正常实例化。"""
    for value in ("remote", "local-gpu", "local-cpu"):
        monkeypatch.setenv("SERVICE__DEFAULT_PROVIDER", value)
        assert Settings().service.default_provider == value


# ---------- Q1-P2：提示头非法取值 → 400 ----------

def test_invalid_provider_hint_rejected():
    """Q1-P2：X-Inference-Provider: local_gpu → 400 INVALID_INPUT，不静默兜底。"""
    resp = _client().post(
        "/infer/segmentation",
        json={"image_url": "http://oss-internal.test/x.png", "elements": ["forest"],
              "class_mapping": {"forest": 1}, "colors": {"forest": "#228B22"},
              "geo_extent": [104.0, 30.0, 104.1, 30.1]},
        headers={"X-Inference-Provider": "local_gpu"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_INPUT"
    assert "local-gpu" in resp.json()["message"]


# ---------- Q1-P3：GPU 兜底 WARN 日志 ----------

def test_gpu_fallback_warn_log():
    """Q1-P3：local-gpu 提示在无 GPU 环境兜底 local-cpu，记 gpu_unavailable_fallback_cpu。"""
    from structlog.testing import capture_logs

    registry.reset_engines()
    with capture_logs() as logs:
        engine = registry.get_engine("local-gpu")
    assert engine.actual_provider == "local-cpu"
    assert any(log["event"] == "gpu_unavailable_fallback_cpu" for log in logs)
    registry.reset_engines()


# ---------- Q2-P1/Q3-P1：GET /infer/models 模型发现 ----------

def test_models_discovery():
    """Q2-P1：发现接口返回伪模型条目——双产物齐全、类别表自 ONNX names、默认版本标记。"""
    resp = _client().get("/infer/models")
    assert resp.status_code == 200
    data = resp.json()
    by_name = {m["name"]: m for m in data["models"]}
    assert "landcover-seg" in by_name and "change-detection" in by_name

    seg_v0 = by_name["landcover-seg"]["versions"][0]
    assert seg_v0["version"] == "v0.0-fake"
    assert seg_v0["fp32"] and seg_v0["int8"]
    assert seg_v0["class_labels"] == ["background", "forest", "grassland", "snow", "building"]
    assert seg_v0["class_count"] == 5  # 4 维逐像素输出 → 通道数 = len(names)
    assert seg_v0["default_for"] == ["segmentation"]

    cd_v0 = by_name["change-detection"]["versions"][0]
    assert cd_v0["class_labels"] is None  # 伪变化检测模型未内嵌 names
    assert cd_v0["default_for"] == ["change_detection"]


@respx.mock
def test_models_discovery_loaded_flag():
    """Q3-P1：推理后对应版本 loaded=true（ModelStore 缓存命中）。"""
    import cv2

    registry.reset_engines()
    img = np.full((64, 64, 3), (20, 200, 20), dtype=np.uint8)
    png = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()
    url = "http://oss-internal.test/z.png"
    respx.get(url).mock(return_value=httpx.Response(200, content=png))

    client = _client()

    def _seg_loaded() -> bool:
        data = client.get("/infer/models").json()
        seg = next(m for m in data["models"] if m["name"] == "landcover-seg")
        return seg["versions"][0]["loaded"]

    assert _seg_loaded() is False
    client.post(
        "/infer/segmentation",
        json={"image_url": url, "elements": ["forest"], "class_mapping": {"forest": 1},
              "colors": {"forest": "#228B22"}, "geo_extent": [104.0, 30.0, 104.1, 30.1]},
    )
    assert _seg_loaded() is True
    registry.reset_engines()


# ---------- Q2-P2：class_ids 自动推导与配置优先 ----------

def test_class_ids_auto_derived_from_onnx_names():
    """Q2-P2：class_ids 缺省时从伪分割模型 names 推导 [0..4]。"""
    store = ModelStore(Settings())
    assert store.effective_class_ids() == [0, 1, 2, 3, 4]
    # 缓存生效（重复调用一致）
    assert store.effective_class_ids() == [0, 1, 2, 3, 4]


def test_class_ids_explicit_config_wins():
    """Q2-P2：显式配置优先于自动推导。"""
    settings = Settings()
    settings.models.segmentation.class_ids = [0, 1]
    store = ModelStore(settings)
    assert store.effective_class_ids() == [0, 1]


def test_class_ids_derive_missing_model_returns_none():
    """模型文件缺失时推导返回 None（不做模型侧类别约束）。"""
    store = ModelStore(Settings())
    assert store.effective_class_ids("no-such-model", "v9.9") is None
