"""M1 骨架冒烟测试：应用可导入、路由已注册（P-006 验收）。"""


def test_app_import_and_routes():
    from compute_service.main import app

    paths = set(app.openapi()["paths"])
    assert "/compute/diff" in paths
    assert "/compute/vectorize" in paths
    assert "/health" in paths


def test_openapi_exportable():
    from compute_service.main import app

    assert app.openapi()["info"]["title"] == "compute-service"
