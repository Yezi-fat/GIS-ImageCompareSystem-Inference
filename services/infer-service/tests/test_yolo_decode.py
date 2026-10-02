"""P-014 真实模型接入验收：YOLO 系检测输出解码（yolo_decode）与引擎双格式分发。

yolo11 v1.0 输出 [1, 84, anchors]（4 框参数 + 80 COCO 类置信度），
解码为 [81, H, W] 概率图：通道 k（0..79）= COCO 类别 k（序号直用），
通道 80（末位）= 背景残差。
本文件全部用合成输出断言，不依赖 228MB 真实模型（真实模型验收在部署侧实测）。
"""
import numpy as np
import pytest

from infer_service.config import Settings
from infer_service.engines.local_onnx import LocalOnnxEngine
from infer_service.engines.model_store import ModelStore
from infer_service.engines.yolo_decode import decode_yolo_detections


def _fake_raw(boxes: list[tuple[float, float, float, float, int, float]], anchors: int = 16) -> np.ndarray:
    """构造 [4+80, anchors] 合成检测输出：boxes = (cx, cy, w, h, cls, conf)。"""
    raw = np.zeros((84, anchors), dtype=np.float32)
    for i, (cx, cy, w, h, cls, conf) in enumerate(boxes):
        raw[0, i], raw[1, i], raw[2, i], raw[3, i] = cx, cy, w, h
        raw[4 + cls, i] = conf
    return raw


# ---------- yolo_decode 单元 ----------

def test_decode_planted_box():
    """单个检测框 → 对应 COCO 通道（序号直用）在框内为置信度、框外为 0，末位背景取残差。"""
    raw = _fake_raw([(32.0, 32.0, 20.0, 10.0, 2, 0.9)])  # COCO cls 2 = car
    prob = decode_yolo_detections(raw, height=64, width=64, conf_threshold=0.25, nms_threshold=0.45)
    assert prob.shape == (81, 64, 64)
    # 通道 2 = COCO 2（直用）：框内 (y 27~37, x 22~42) 置信度 0.9
    assert prob[2, 32, 32] == pytest.approx(0.9)
    assert prob[2, 60, 60] == 0.0
    # 背景通道（末位 80）：框内 ≈ 0.1，框外 = 1.0
    assert prob[80, 32, 32] == pytest.approx(0.1, abs=1e-6)
    assert prob[80, 60, 60] == pytest.approx(1.0)


def test_decode_conf_threshold_filter():
    """低于置信度阈值的检测被过滤 → 全背景。"""
    raw = _fake_raw([(32.0, 32.0, 20.0, 10.0, 0, 0.10)])
    prob = decode_yolo_detections(raw, 64, 64, 0.25, 0.45)
    assert prob[:80].max() == 0.0
    assert prob[80].min() == pytest.approx(1.0)


def test_decode_nms_dedup_same_class():
    """同类重叠框经 NMS 去重：区域置信度取保留框（更高分）且不重复计数。"""
    raw = _fake_raw([
        (32.0, 32.0, 20.0, 20.0, 0, 0.90),
        (33.0, 33.0, 20.0, 20.0, 0, 0.80),   # 与上一框 IoU≈0.68 > 0.45 → 被抑制
    ])
    prob = decode_yolo_detections(raw, 64, 64, 0.25, 0.45)
    assert prob[0, 32, 32] == pytest.approx(0.90)  # 取最大置信度框


def test_decode_overlapping_diff_class_kept():
    """异类重叠框各自保留（逐类 NMS），重叠区各类通道各自置置信度。"""
    raw = _fake_raw([
        (32.0, 32.0, 20.0, 20.0, 0, 0.90),   # person
        (32.0, 32.0, 20.0, 20.0, 2, 0.70),   # car
    ])
    prob = decode_yolo_detections(raw, 64, 64, 0.25, 0.45)
    assert prob[0, 32, 32] == pytest.approx(0.90)
    assert prob[2, 32, 32] == pytest.approx(0.70)
    assert prob[80, 32, 32] == pytest.approx(0.10, abs=1e-6)  # 背景残差 = 1 - max


def test_decode_box_clipped_to_tile():
    """越界框裁剪到 Tile 范围（不产生负索引回绕）。"""
    raw = _fake_raw([(2.0, 2.0, 20.0, 20.0, 5, 0.9)])  # 框超出左上边界
    prob = decode_yolo_detections(raw, 64, 64, 0.25, 0.45)
    assert prob[5, 0, 0] == pytest.approx(0.9)
    assert prob.shape[1:] == (64, 64)


# ---------- 引擎双格式分发 ----------

class _StubSession:
    """按预设输出响应 run() 的假 InferenceSession。"""

    def __init__(self, output: np.ndarray):
        self._output = output

    def get_inputs(self):
        class _In:
            name = "images"
        return [_In()]

    def run(self, _out, feed):
        return [self._output]


def _engine_with_output(output: np.ndarray, class_ids: list[int]) -> LocalOnnxEngine:
    settings = Settings()
    settings.models.segmentation.class_ids = class_ids
    engine = LocalOnnxEngine(settings, ModelStore(settings), "local-cpu")
    engine._session = lambda *a, **k: _StubSession(output)  # type: ignore[method-assign]
    return engine


def test_engine_dispatch_detection_output():
    """检测输出 [1,84,anchors] → 解码为 [81,H,W] 概率图（COCO 序号直用）。"""
    raw = _fake_raw([(8.0, 8.0, 6.0, 6.0, 0, 0.95)])[np.newaxis, ...]  # 加 batch 维
    engine = _engine_with_output(raw, class_ids=list(range(81)))
    tile = np.zeros((3, 16, 16), dtype=np.float32)
    prob = engine.segment([tile])[0]
    assert prob.shape == (81, 16, 16)
    assert prob[0, 8, 8] == pytest.approx(0.95)   # person = 通道 0（直用）
    assert prob[80, 8, 8] == pytest.approx(0.05, abs=1e-6)  # 背景残差


def test_engine_dispatch_probmap_output():
    """概率图输出 [1,C,H,W]（伪模型形态）→ 直接透传，不走检测解码。"""
    pmap = np.random.default_rng(0).random((1, 5, 16, 16), dtype=np.float32)
    engine = _engine_with_output(pmap, class_ids=[0, 1, 2, 3, 4])
    tile = np.zeros((3, 16, 16), dtype=np.float32)
    prob = engine.segment([tile])[0]
    assert prob.shape == (5, 16, 16)
    np.testing.assert_array_equal(prob, pmap[0])


def test_engine_channel_mismatch_rejected():
    """模型输出通道数与 class_ids 配置错配 → INFERENCE_FAILED（不静默错切类别）。"""
    from cv_common.errors import InferenceFailedError

    pmap = np.zeros((1, 5, 16, 16), dtype=np.float32)
    engine = _engine_with_output(pmap, class_ids=list(range(81)))  # 期望 81 通道
    tile = np.zeros((3, 16, 16), dtype=np.float32)
    with pytest.raises(InferenceFailedError):
        engine.segment([tile])
