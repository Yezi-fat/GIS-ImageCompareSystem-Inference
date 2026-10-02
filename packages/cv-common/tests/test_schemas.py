"""cv-common schema 契约测试（P-002/P-003 验收：模型可实例化，字段与设计 §4 逐一对应）。"""
from cv_common.schemas.change import ChangeDetectRequest, ChangeMaskResponse
from cv_common.schemas.diff import DiffRequest
from cv_common.schemas.nlp import NlParseRequest
from cv_common.schemas.segmentation import SegmentationRequest
from cv_common.schemas.vectorize import VectorizeRequest


def test_segmentation_request_fields():
    """设计 §4.1 + 评审 D-01（geo_extent 必填）。"""
    req = SegmentationRequest(
        image_url="http://oss-internal/x/t1.png",
        elements=["forest", "building"],
        class_mapping={"forest": 1, "building": 3},
        colors={"forest": "#228B22", "building": "#CD853F"},
        geo_extent=[104.01, 30.69, 104.07, 30.73],
    )
    assert req.min_area == 100
    assert req.model_name is None  # 缺省用默认版本（FR-3.5）


def test_change_detect_request_fields():
    """设计 §4.2 + 评审 D-01/D-03（geo_extent 必填、auto_align、estimated_shift_px 在响应）。"""
    req = ChangeDetectRequest(
        before_url="http://oss-internal/x/t1.png",
        after_url="http://oss-internal/x/t2.png",
        geo_extent=[104.01, 30.69, 104.07, 30.73],
    )
    assert req.threshold == 0.5 and req.auto_align is False
    resp_fields = set(ChangeMaskResponse.model_fields)
    assert {"estimated_shift_px", "auto_aligned", "mask_png_b64", "probmap_png_b64"} <= resp_fields


def test_diff_request_defaults():
    """设计 §4.3：diff 分色默认绿/红（FR-7.3）。"""
    req = DiffRequest(before_mask_b64="a", after_mask_b64="b", element="forest")
    assert req.colors.added == "#00FF00" and req.colors.removed == "#FF0000"
    assert req.geo_transform is None


def test_vectorize_request_defaults():
    req = VectorizeRequest(mask_b64="a")
    assert req.min_area == 100.0


def test_nlp_parse_request_requires_catalog():
    """评审 D-02/J-06：element_catalog 必填；响应含 uncertain_fields（可空）。"""
    req = NlParseRequest(
        text="识别四川成都金牛区xx街道的建筑群",
        element_catalog=[{"id": "building", "name": "建筑", "model_class_id": 4}],
    )
    assert req.element_catalog[0].enabled is True
