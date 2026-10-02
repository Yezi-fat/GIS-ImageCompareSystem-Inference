"""P-026 性能基准（pytest-benchmark）：单 Tile / 1024² 图 CPU 基线。

基线报告产出：pytest --benchmark-save=baseline（存 benchmarks/）；
回归校验：scripts/check_benchmark.py（均值劣化 >20% 报警退出 1）。
真实模型交付后基线须重录（伪模型基线仅防管线退化）。
"""
import cv2
import numpy as np
import pytest
import respx
import httpx

from infer_service.pipeline import preprocess, tiler

URL = "http://oss-internal.test/bench.png?sign=x"
GEO_EXTENT = [104.0, 30.0, 104.1, 30.1]


@pytest.fixture(autouse=True)
def _reset():
    from infer_service.engines import registry

    registry.reset_engines()
    yield
    registry.reset_engines()


def _scene_png(size: int) -> bytes:
    rng = np.random.default_rng(7)
    img = rng.integers(0, 255, (size, size, 3)).astype(np.uint8)
    return cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))[1].tobytes()


@pytest.mark.benchmark(group="tile")
def test_single_tile_segment_cpu(benchmark):
    """单 Tile（256×256）分割推理基线（需求 8.1：CPU INT8 ≤ 3s，伪模型远低于此）。"""
    from infer_service.engines import registry

    engine = registry.get_engine("local-cpu")
    tile = preprocess.preprocess_tile(np.zeros((256, 256, 3), dtype=np.uint8))
    benchmark(lambda: engine.segment([tile]))
    assert benchmark.stats["mean"] < 3.0  # 性能红线（需求 8.1）


@pytest.mark.benchmark(group="e2e")
@respx.mock
def test_segmentation_1024_e2e(benchmark):
    """1024×1024 要素识别端到端基线（需求 8.1：CPU ≤ 60s）。"""
    respx.get(URL).mock(return_value=httpx.Response(200, content=_scene_png(1024)))
    from cv_common.schemas.segmentation import SegmentationRequest

    from infer_service.analysis.segmentation import run_segmentation

    req = SegmentationRequest(
        image_url=URL, elements=["forest"], class_mapping={"forest": 1},
        colors={"forest": "#228B22"}, geo_extent=GEO_EXTENT,
    )
    benchmark(run_segmentation, req)
    assert benchmark.stats["mean"] < 60.0


@pytest.mark.benchmark(group="pipeline")
def test_tiler_fuse_1024(benchmark):
    """tiler 切分+融合 1024² 基线（本服务自身开销口径，需求 8.1 ≤2s@4096²）。"""
    img = np.zeros((1024, 1024, 3), dtype=np.uint8)

    def _run():
        canvas, count = tiler.alloc_canvas(1024, 1024, 5)
        prob = np.full((5, 256, 256), 0.5, dtype=np.float32)
        for _, offset in tiler.split_tiles(img, 256, 32):
            tiler.fuse_tile(canvas, prob, offset, count)
        return tiler.finalize_canvas(canvas, count)

    benchmark(_run)
    assert benchmark.stats["mean"] < 2.0
