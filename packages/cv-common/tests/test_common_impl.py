"""cv-common 实现测试（P-009 验收）：geo 大地测量、imaging 编解码、
errors 统一处理、auth 两态行为、logging JSON + task_id 透传。
"""
import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cv_common.auth import auth_middleware
from cv_common.errors import (
    AlignmentFailedError,
    CvError,
    GeoExtentMismatchError,
    ImageDecodeFailedError,
    InferenceFailedError,
    InputTooLargeError,
    LocationUnresolvedError,
    ModelNotReadyError,
    NluUnavailableError,
    UnsupportedElementError,
    register_error_handlers,
)
from cv_common.geo import geodesic_area, pixel_area_m2
from cv_common.imaging import decode_b64_png, encode_png_b64


class TestGeodesicArea:
    def test_known_area(self):
        """赤道处 1°×1° 方格 ≈ 12309 km²（CGCS2000 椭球）。"""
        from shapely.geometry import box

        area = geodesic_area(box(0.0, 0.0, 1.0, 1.0))
        assert abs(area - 1.2309e10) / 1.2309e10 < 0.001

    def test_hole_subtracted(self):
        from shapely.geometry import Polygon

        shell = [(0, 0), (2, 0), (2, 2), (0, 2)]
        hole = [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5)]
        poly = Polygon(shell, [hole])
        assert geodesic_area(poly) < geodesic_area(Polygon(shell))

    def test_pixel_area(self):
        """像素面积随纬度增大而减小（row 越大纬度越低、面积越大）。"""
        gt = [104.0, 0.001, 0.0, 31.0, 0.0, -0.001]
        assert pixel_area_m2(gt, row=500, col=0) > pixel_area_m2(gt, row=0, col=0)


class TestImaging:
    def test_roundtrip(self):
        arr = (np.random.rand(64, 64, 4) * 255).astype(np.uint8)
        assert np.array_equal(decode_b64_png(encode_png_b64(arr)), arr)

    def test_decode_invalid(self):
        with pytest.raises(ImageDecodeFailedError):
            decode_b64_png("aGVsbG8=")  # 合法 base64 但非 PNG


class TestErrors:
    def _app(self) -> FastAPI:
        app = FastAPI()
        register_error_handlers(app)

        @app.get("/boom")
        def boom():
            raise UnsupportedElementError("类别 snow 超出模型输出")

        @app.get("/unhandled")
        def unhandled():
            raise RuntimeError("unexpected")

        return app

    def test_cv_error_json(self):
        resp = TestClient(self._app()).get("/boom")
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "UNSUPPORTED_ELEMENT"
        assert "snow" in body["message"]
        assert body["trace_id"]

    def test_unhandled_internal_error(self):
        resp = TestClient(self._app(), raise_server_exceptions=False).get("/unhandled")
        assert resp.status_code == 500
        assert resp.json()["code"] == "INTERNAL_ERROR"

    def test_nine_concrete_errors(self):
        """评审 D-03/04/05：九个子类码值与 HTTP 状态对照设计 §4.6。"""
        expected = {
            UnsupportedElementError: ("UNSUPPORTED_ELEMENT", 400),
            ImageDecodeFailedError: ("IMAGE_DECODE_FAILED", 400),
            GeoExtentMismatchError: ("GEO_EXTENT_MISMATCH", 400),
            ModelNotReadyError: ("MODEL_NOT_READY", 503),
            InferenceFailedError: ("INFERENCE_FAILED", 500),
            NluUnavailableError: ("NLU_UNAVAILABLE", 503),
            InputTooLargeError: ("INPUT_TOO_LARGE", 400),
            LocationUnresolvedError: ("LOCATION_UNRESOLVED", 422),
            AlignmentFailedError: ("ALIGNMENT_FAILED", 422),
        }
        for cls, (code, status) in expected.items():
            e = cls("m")
            assert isinstance(e, CvError) and e.code == code and e.http_status == status


class TestAuth:
    """P-009 验收：开关两态行为正确（true 无 token 401 / false 无 token 放行；/health 恒豁免）。"""

    def _app(self, enabled: bool) -> FastAPI:
        app = FastAPI()

        @app.get("/api")
        def api():
            return {"ok": True}

        @app.get("/health")
        def health():
            return {"status": "ok"}

        auth_middleware(app, enabled=enabled, internal_token="secret-token")
        return app

    def test_enabled_without_token_401(self):
        client = TestClient(self._app(enabled=True))
        resp = client.get("/api")
        assert resp.status_code == 401 and resp.json()["code"] == "UNAUTHORIZED"

    def test_enabled_with_token_pass(self):
        client = TestClient(self._app(enabled=True))
        assert client.get("/api", headers={"X-Internal-Token": "secret-token"}).status_code == 200
        assert client.get("/api", headers={"X-Internal-Token": "wrong"}).status_code == 401

    def test_enabled_health_exempt(self):
        client = TestClient(self._app(enabled=True))
        assert client.get("/health").status_code == 200

    def test_disabled_pass_through(self):
        client = TestClient(self._app(enabled=False))
        assert client.get("/api").status_code == 200


class TestLogging:
    def test_json_with_service_and_task_id(self, capsys):
        """P-009 验收：日志 JSON 含 service 与 task_id 字段。"""
        import structlog

        from cv_common.logging import configure_logging

        configure_logging("test-svc")
        structlog.contextvars.bind_contextvars(task_id="task-123")
        structlog.get_logger().info("hello", extra_field=1)

        line = capsys.readouterr().out.strip().splitlines()[-1]
        record = json.loads(line)
        assert record["service"] == "test-svc"
        assert record["task_id"] == "task-123"
        assert record["event"] == "hello" and record["level"] == "info"
        structlog.contextvars.clear_contextvars()

    def test_middleware_binds_headers(self):
        import structlog

        from cv_common.logging import configure_logging, task_id_middleware

        configure_logging("mw-svc")
        app = FastAPI(title="mw-svc")

        @app.get("/whoami")
        def whoami():
            ctx = structlog.contextvars.get_contextvars()
            return {"task_id": ctx.get("task_id"), "trace_id": ctx.get("trace_id")}

        task_id_middleware(app)
        resp = TestClient(app).get("/whoami", headers={"X-Task-Id": "t1", "X-Trace-Id": "tr1"})
        assert resp.json() == {"task_id": "t1", "trace_id": "tr1"}
