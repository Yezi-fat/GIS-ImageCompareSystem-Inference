"""M1 骨架冒烟测试：应用可导入、路由已注册、OpenAPI 可导出（P-005 验收）。"""


def test_app_import_and_routes():
    from infer_service.main import app

    paths = set(app.openapi()["paths"])
    assert "/infer/segmentation" in paths
    assert "/infer/change-detection" in paths
    assert "/health" in paths


def test_openapi_exportable():
    from infer_service.main import app

    spec = app.openapi()
    assert spec["info"]["title"] == "infer-service"
