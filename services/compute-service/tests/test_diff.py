"""P-019 验收：三态划分（面积精确可断言）、边缘伪差异抑制、分色蒙版、
双单位统计（px + m²）、geo_transform 经 /compute/diff 全链路。

合成用例：before = [100×100 方块]，after = 方块右移 20px
→ added = 100×20 = 2000px，removed = 100×20 = 2000px（腐蚀后略小，单独断言腐蚀语义）。
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from cv_common.imaging import decode_b64_png, encode_png_b64
from cv_common.schemas.diff import DiffColors

from compute_service.analysis.differ import compute_diff, edge_erode

GEO_TRANSFORM = [104.0, 0.001, 0.0, 31.0, 0.0, -0.001]  # 100×100 图：104~104.1, 30.9~31.0


def _shift_masks():
    """方块右移 20px（避开图像边界，保证 cv2.erode 边界语义对称）。"""
    before = np.zeros((100, 100), dtype=np.uint8)
    before[20:80, 10:70] = 1                       # 60×60
    after = np.zeros((100, 100), dtype=np.uint8)
    after[20:80, 30:90] = 1                        # 右移 20px
    return before, after


class TestDiffThreeState:
    """合成用例三态面积精确可断言（无腐蚀/过滤干扰：min_area=0 + 先验形状）。"""

    def test_added_removed_exact(self):
        before, after = _shift_masks()
        # 边缘腐蚀 1px：added/removed 各向内缩 1px
        _, stats = compute_diff(before, after, min_area=0, colors=DiffColors())
        # added 原 60×20=1200 → 腐蚀后 58×18=1044；removed 同
        assert stats.added_px == 58 * 18
        assert stats.removed_px == 58 * 18
        # net/rate 基于原始两期面积（3600 → 3600）
        assert stats.net_change_px == 0 and stats.change_rate == 0.0

    def test_pure_addition(self):
        before = np.zeros((50, 50), dtype=np.uint8)
        after = np.zeros((50, 50), dtype=np.uint8)
        after[10:30, 10:30] = 1
        _, stats = compute_diff(before, after, min_area=0, colors=DiffColors())
        assert stats.removed_px == 0
        assert stats.added_px == 18 * 18  # 400 腐蚀 1px → 18²
        assert stats.change_rate == 1.0   # before=0 从无到有

    def test_min_area_filter(self):
        """FR-7.4：小于 min_area 的差异斑块被过滤。"""
        before, after = _shift_masks()
        _, stats = compute_diff(before, after, min_area=2000, colors=DiffColors())
        assert stats.added_px == 0 and stats.removed_px == 0

    def test_colorize(self):
        """FR-7.3：新增绿、减少红、不变透明。"""
        before, after = _shift_masks()
        mask, _ = compute_diff(before, after, min_area=0, colors=DiffColors())
        assert mask.shape == (100, 100, 4)
        assert tuple(mask[50, 80]) == (0x00, 0xFF, 0x00, 255)   # added（右缘新增区）
        assert tuple(mask[50, 15]) == (0xFF, 0x00, 0x00, 255)   # removed（左缘减少区）
        assert tuple(mask[50, 50]) == (0, 0, 0, 0)              # 不变区域透明

    def test_custom_colors(self):
        """颜色语义可配置（FR-7.3）。"""
        before, after = _shift_masks()
        mask, _ = compute_diff(before, after, min_area=0,
                               colors=DiffColors(added="#0000FF", removed="#FFFF00"))
        assert tuple(mask[50, 80]) == (0x00, 0x00, 0xFF, 255)
        assert tuple(mask[50, 15]) == (0xFF, 0xFF, 0x00, 255)

    def test_area_m2_dual_unit(self):
        """有 geo_transform 时输出 m² 双单位（CGCS2000 椭球逐行积分）。"""
        before, after = _shift_masks()
        _, stats = compute_diff(before, after, min_area=0, colors=DiffColors(),
                                geo_transform=GEO_TRANSFORM)
        assert stats.added_m2 and stats.added_m2 > 0
        # 逐像素面积 ≈ 0.001°×0.001° 在纬度 ~30.95 ≈ 10590 m²/px 量级 ×1044 px
        assert stats.added_m2 == pytest.approx(58 * 18 * 10590, rel=0.05)

    def test_shape_mismatch(self):
        from cv_common.errors import GeoExtentMismatchError

        with pytest.raises(GeoExtentMismatchError):
            compute_diff(np.zeros((10, 10), dtype=np.uint8), np.zeros((10, 20), dtype=np.uint8),
                         0, DiffColors())

    def test_edge_erode(self):
        mask = np.zeros((20, 20), dtype=np.uint8)
        mask[5:15, 5:15] = 1
        eroded = edge_erode(mask, px=1)
        assert eroded.sum() == 64   # 10×10 → 8×8
        assert edge_erode(mask, px=0).sum() == 100


class TestDiffApi:
    """/compute/diff 全链路（含 base64 编解码）。"""

    @pytest.fixture()
    def client(self):
        from compute_service.main import app

        return TestClient(app)

    def test_diff_endpoint(self, client):
        before, after = _shift_masks()
        resp = client.post("/compute/diff", json={
            "before_mask_b64": encode_png_b64(before * 255),
            "after_mask_b64": encode_png_b64(after * 255),
            "element": "forest",
            "min_area": 0,
            "geo_transform": GEO_TRANSFORM,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["statistics"]["added_px"] == 58 * 18
        assert data["statistics"]["added_m2"] > 0
        # 分色蒙版可解码
        mask = decode_b64_png(data["diff_mask_png_b64"])
        assert mask.shape == (100, 100, 4)
