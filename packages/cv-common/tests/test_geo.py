"""cv-common 单元测试（M1：纯公式函数；M2 起随 P-009 补全实现测试）。

测试用合成小尺寸数据，不依赖真实模型（全局要求 #5）。
"""
from cv_common.geo import extent_to_geo_transform, tile_range_to_extent, tile_span


class TestTileSpan:
    """P-004 验收：tile_span(z) == 360 / 2**z。"""

    def test_level_0(self):
        assert tile_span(0) == 360.0

    def test_level_16(self):
        assert tile_span(16) == 360.0 / 2**16

    def test_span_base_configurable(self):
        """span_base 配置化（FR-10.6 瓦片矩阵参数）。"""
        assert tile_span(1, span_base=180.0) == 90.0


class TestTileRangeToExtent:
    """瓦片范围 → geo_extent 线性公式（纯公式函数）。"""

    def test_whole_world_z1(self):
        # z=1 瓦片跨度 180°；x 取 0~1 两列、y 取 0 一行即覆盖全球（纯线性公式，纬度不截断）
        assert tile_range_to_extent(1, 0, 1, 0, 0) == [-180.0, -90.0, 180.0, 90.0]

    def test_single_tile_z1(self):
        # z=1 瓦片跨度 180°；左上角瓦片 (x=0, y=0)
        assert tile_range_to_extent(1, 0, 0, 0, 0) == [-180.0, 90.0 - 180.0, 0.0, 90.0]

    def test_origin_configurable(self):
        extent = tile_range_to_extent(1, 0, 0, 0, 0, origin=(0.0, 90.0))
        assert extent[0] == 0.0 and extent[2] == 180.0


class TestExtentToGeoTransform:
    """geo_extent + 图像宽高 → GDAL 六参数（评审 D-01，纯公式函数）。"""

    def test_linear_mapping(self):
        gt = extent_to_geo_transform([104.0, 30.0, 105.0, 31.0], width=1000, height=500)
        assert gt == [104.0, 0.001, 0.0, 31.0, 0.0, -0.002]

    def test_roundtrip_consistency(self):
        """推导结果覆盖范围与输入 geo_extent 一致。"""
        extent = [104.01, 30.69, 104.07, 30.73]
        w, h = 2400, 1600
        gt = extent_to_geo_transform(extent, w, h)
        assert abs(gt[0] + w * gt[1] - extent[2]) < 1e-12
        assert abs(gt[3] + h * gt[5] - extent[1]) < 1e-12
