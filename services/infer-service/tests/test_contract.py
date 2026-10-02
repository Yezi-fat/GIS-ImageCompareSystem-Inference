"""联调契约锁定测试（《Python侧待办事项-回复》第 1 节，Java 已适配口径勿变）：

1. pydantic 可选字段「缺省即默认」，显式 null → 422 `{"detail":[...]}`
   （Java 以 @JsonInclude(NON_NULL) 规避并将该形态映射 INVALID_INPUT）；
2. 403 预签名过期 message 稳定含「403」字样（Java 据此重签重试）——
   由 test_loader.py::test_url_expired_403 锁定，此处不重复。
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from infer_service.main import app

    return TestClient(app)


def test_explicit_null_rejected_422_detail(client):
    """显式 null（如 min_area: null）→ 422 且响应为 FastAPI detail 形态（不进入业务逻辑）。"""
    resp = client.post("/infer/segmentation", json={
        "image_url": "http://oss-internal.test/x.png",
        "elements": ["forest"],
        "class_mapping": {"forest": 1},
        "colors": {"forest": "#228B22"},
        "geo_extent": [104.0, 30.0, 104.1, 30.1],
        "min_area": None,
    })
    assert resp.status_code == 422
    assert "detail" in resp.json()  # 契约形态锁定：Java PythonErrorDecoder 按此映射


def test_missing_required_field_422_detail(client):
    """缺必填字段（geo_extent）→ 同形态 422。"""
    resp = client.post("/infer/segmentation", json={
        "image_url": "http://oss-internal.test/x.png",
        "elements": ["forest"],
        "class_mapping": {"forest": 1},
        "colors": {"forest": "#228B22"},
    })
    assert resp.status_code == 422 and "detail" in resp.json()
