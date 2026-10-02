"""P-011 验收：tiler 无缝拼接（重叠区均值融合断言）、4096² 流式内存有界、
postprocess 小区域过滤、colorize 颜色映射。
"""
import numpy as np

from infer_service.pipeline import colorize, postprocess, tiler


class TestTiler:
    def test_split_covers_all_and_overlaps(self):
        """切分全覆盖且相邻 Tile 存在重叠区。"""
        img = np.zeros((512, 512, 3), dtype=np.uint8)
        tiles = list(tiler.split_tiles(img, tile_size=256, overlap=32))
        # (512-256)/224 = 1.14 → 起点 0,224 + 回缩 256 → 每轴 3 块
        assert len(tiles) == 9
        offsets = {off for _, off in tiles}
        assert (0, 0) in offsets and (256, 256) in offsets  # 边缘回缩对齐

    def test_fusion_seamless(self):
        """重叠区均值融合：常量概率图拼接后处处等于常量（无接缝）。"""
        h, w, c = 512, 512, 5
        img = np.zeros((h, w, 3), dtype=np.uint8)
        canvas, count = tiler.alloc_canvas(h, w, c)
        const_prob = np.full((c, 256, 256), 0.7, dtype=np.float32)
        for _, offset in tiler.split_tiles(img, 256, 32):
            tiler.fuse_tile(canvas, const_prob.copy(), offset, count)
        probs = tiler.finalize_canvas(canvas, count)
        assert probs.shape == (c, h, w)
        assert np.allclose(probs, 0.7, atol=1e-6)  # 重叠区断言：无接缝

    def test_overlap_region_is_mean(self):
        """重叠区为两 Tile 均值。"""
        h, w, c = 256, 480, 2  # 两块 256，重叠 32px
        img = np.zeros((h, w, 3), dtype=np.uint8)
        canvas, count = tiler.alloc_canvas(h, w, c)
        for tile_prob, offset in [
            (np.zeros((c, 256, 256), dtype=np.float32), (0, 0)),
            (np.ones((c, 256, 256), dtype=np.float32), (0, 224)),
        ]:
            tiler.fuse_tile(canvas, tile_prob, offset, count)
        probs = tiler.finalize_canvas(canvas, count)
        assert probs[0, 0, 100] == 0.0            # 非重叠（左块）
        assert probs[0, 0, 240] == 0.5            # 重叠区均值
        assert probs[0, 0, 400] == 1.0            # 非重叠（右块）

    def test_4096_streaming_bounded(self):
        """4096² 大图流式切分：生成器逐块产出，不同时持有全部 Tile（内存有界）。"""
        img = np.zeros((4096, 4096, 3), dtype=np.uint8)  # 48MB
        gen = tiler.split_tiles(img, 256, 32)
        assert not isinstance(gen, list)  # 生成器惰性
        count = sum(1 for _ in gen)
        # (4096-256)/224 = 17.14 → 起点 0..3808 共 18 个 + 回缩 3840 → 19 块/轴
        assert count == 19 * 19


class TestPostprocess:
    def test_remove_small_regions(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[10:20, 10:20] = 1   # 100 px
        mask[60, 60] = 1         # 1 px
        mask[70:80, 70:80] = 1   # 100 px
        out = postprocess.remove_small_regions(mask, min_area=50)
        assert out[60, 60] == 0 and out[15, 15] == 1 and out[75, 75] == 1

    def test_morphology_denoise(self):
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[40:60, 40:60] = 1
        mask[5, 5] = 1  # 孤立噪点
        out = postprocess.postprocess_mask(mask, min_area=10)
        assert out[5, 5] == 0 and out[50, 50] == 1


class TestColorize:
    def test_colorize_combined(self):
        masks = {
            "forest": np.array([[1, 0], [0, 0]], dtype=np.uint8),
            "building": np.array([[0, 1], [0, 0]], dtype=np.uint8),
        }
        colors = {"forest": "#228B22", "building": "#CD853F"}
        rgba = colorize.colorize_combined(masks, colors)
        assert rgba.shape == (2, 2, 4)
        assert tuple(rgba[0, 0]) == (0x22, 0x8B, 0x22, 255)
        assert tuple(rgba[0, 1]) == (0xCD, 0x85, 0x3F, 255)
        assert tuple(rgba[1, 1]) == (0, 0, 0, 0)  # 非覆盖区域全透明

    def test_hex_validation(self):
        import pytest

        from cv_common.errors import CvError

        with pytest.raises(CvError):
            colorize.hex_to_rgba("red")
