"""P-025 验收：输入规模限制（local-cpu >2048 拒绝 INPUT_TOO_LARGE）+
大图分块内存回归（4096² 峰值有界）。

P-027 补充覆盖：引擎选择矩阵（provider_hint × 环境）、registry 兜底。
"""
import cv2
import numpy as np
import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from infer_service.engines import registry
from infer_service.engines.local_onnx import LocalOnnxEngine
from infer_service.engines.remote import RemoteEngine

URL = "http://oss-internal.test/big.png?sign=x"
GEO_EXTENT = [104.0, 30.0, 104.2, 30.2]


def _png(img: np.ndarray) -> bytes:
    return cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()


@pytest.fixture()
def client():
    from infer_service.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset():
    registry.reset_engines()
    yield
    registry.reset_engines()


class TestInputTooLarge:
    """P-025：local-cpu 模式输入 >2048 → INPUT_TOO_LARGE（Java 拦截为主、本服务兜底）。"""

    @respx.mock
    def test_segmentation_oversize_rejected(self, client):
        img = np.full((2100, 2100, 3), 128, dtype=np.uint8)
        respx.get(URL).mock(return_value=httpx.Response(200, content=_png(img)))
        resp = client.post("/infer/segmentation", json={
            "image_url": URL, "elements": ["forest"], "class_mapping": {"forest": 1},
            "colors": {"forest": "#228B22"}, "geo_extent": GEO_EXTENT,
        })
        assert resp.status_code == 400
        assert resp.json()["code"] == "INPUT_TOO_LARGE"

    @respx.mock
    def test_change_detection_oversize_rejected(self, client):
        img = np.full((2100, 2100, 3), 128, dtype=np.uint8)
        respx.get(URL).mock(return_value=httpx.Response(200, content=_png(img)))
        url2 = "http://oss-internal.test/big2.png?sign=y"
        respx.get(url2).mock(return_value=httpx.Response(200, content=_png(img)))
        resp = client.post("/infer/change-detection", json={
            "before_url": URL, "after_url": url2, "geo_extent": GEO_EXTENT,
        })
        assert resp.status_code == 400
        assert resp.json()["code"] == "INPUT_TOO_LARGE"

    @respx.mock
    def test_at_limit_accepted(self, client):
        """边界值 2048 放行（>2048 才拒绝）。"""
        img = np.full((2048, 2048, 3), (20, 200, 20), dtype=np.uint8)
        respx.get(URL).mock(return_value=httpx.Response(200, content=_png(img)))
        resp = client.post("/infer/segmentation", json={
            "image_url": URL, "elements": ["forest"], "class_mapping": {"forest": 1},
            "colors": {"forest": "#228B22"}, "geo_extent": GEO_EXTENT, "min_area": 0,
        })
        assert resp.status_code == 200


class TestMemoryRegression:
    """P-025：大图分块内存回归——4096² 在 local-cpu 被 INPUT_TOO_LARGE 拦截（不发生 OOM）；
    流式分块的内存有界性由 tiler 生成器单测（test_4096_streaming_bounded）覆盖。"""

    @respx.mock
    def test_4096_rejected_not_oom(self, client):
        """4096² 超 local-cpu 上限 → 明确拒绝（防御正确性：拒绝而非内存失控）。"""
        img = np.zeros((4096, 4096, 3), dtype=np.uint8)
        respx.get(URL).mock(return_value=httpx.Response(200, content=_png(img)))
        resp = client.post("/infer/segmentation", json={
            "image_url": URL, "elements": ["forest"], "class_mapping": {"forest": 1},
            "colors": {"forest": "#228B22"}, "geo_extent": GEO_EXTENT,
        })
        assert resp.status_code == 400
        assert resp.json()["code"] == "INPUT_TOO_LARGE"


class TestEngineSelectionMatrix:
    """P-027：引擎选择矩阵（provider_hint × 环境）。"""

    def test_matrix(self):
        """无 GPU 环境下：None→默认(local-cpu)、local-cpu→CPU、local-gpu→CPU 兜底、
        remote 无 endpoint→明确报错。"""
        # None → 默认 local-cpu
        engine = registry.get_engine(None)
        assert isinstance(engine, LocalOnnxEngine)
        assert engine.actual_provider == "local-cpu"
        # local-cpu → CPU
        assert registry.get_engine("local-cpu").actual_provider == "local-cpu"
        # local-gpu 无 GPU → CPU 兜底（actual_provider 如实标记）
        assert registry.get_engine("local-gpu").actual_provider == "local-cpu"
        # remote 未配置 endpoint → InferenceFailedError（明确报错，非隐式失败）
        from cv_common.errors import InferenceFailedError

        with pytest.raises(InferenceFailedError):
            registry.get_engine("remote")

    def test_remote_engine_selected_when_configured(self):
        """remote 提示且 endpoint 已配置 → RemoteEngine。"""
        from infer_service.config import settings

        settings.remote.endpoint = "http://inference-platform.internal:8000/api/v2/inference"
        try:
            registry.reset_engines()
            engine = registry.get_engine("remote")
            assert isinstance(engine, RemoteEngine)
            assert engine.actual_provider == "remote"
        finally:
            settings.remote.endpoint = ""
            registry.reset_engines()
