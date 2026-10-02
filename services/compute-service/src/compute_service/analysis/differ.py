"""双期差异逐像素计算（设计 §3.3.3，FR-7.2/7.4/7.5，P-019 实现）。

纯像素计算，不调模型：

    added    = after_mask & ~before_mask     # 新增：仅新图有
    removed  = before_mask & ~after_mask     # 减少：仅旧图有
    change_rate = (after - before) / before

边缘伪差异抑制（FR-7.7）：对蒙版边界 1~2 像素做腐蚀后再比对，
配准误差导致的边缘噪声被过滤。
"""
from __future__ import annotations

import cv2
import numpy as np

from cv_common.errors import CvError, GeoExtentMismatchError
from cv_common.geo import pixel_area_m2
from cv_common.schemas.diff import DiffColors, DiffStatistics

_ERODE_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))


def edge_erode(mask: np.ndarray, px: int = 1) -> np.ndarray:
    """蒙版边界腐蚀，抑制配准误差导致的边缘伪差异（FR-7.7）。"""
    if px <= 0 or not mask.any():
        return (mask > 0).astype(np.uint8)
    return cv2.erode((mask > 0).astype(np.uint8), _ERODE_KERNEL, iterations=px)


def compute_diff(
    before_mask: np.ndarray,
    after_mask: np.ndarray,
    min_area: int,
    colors: DiffColors,
    geo_transform: list[float] | None = None,
) -> tuple[np.ndarray, DiffStatistics]:
    """三态划分 → 边缘伪差异抑制 → 后处理（小区域过滤 FR-7.4）→ 分色蒙版 + 统计（FR-7.5）。

    返回 (RGBA 分色差异蒙版, 统计)：added/removed 按 colors 分色、不变区域透明；
    有 geo_transform 时按 CGCS2000 椭球逐行积分补 added_m2/removed_m2。
    双期影像尺寸不一致 → GeoExtentMismatchError。
    """
    before = (before_mask > 0).astype(np.uint8)
    after = (after_mask > 0).astype(np.uint8)
    if before.shape != after.shape:
        raise GeoExtentMismatchError(
            f"双期蒙版尺寸不一致：{before.shape} vs {after.shape}（须同源同层级瓦片）"
        )

    # 三态划分（FR-7.2）→ 边缘伪差异抑制（FR-7.7）→ 小区域过滤（FR-7.4）
    added = _remove_small_regions(edge_erode(after & ~before), min_area)
    removed = _remove_small_regions(edge_erode(before & ~after), min_area)

    diff_mask = _colorize_diff(added, removed, colors)

    # 统计（FR-7.5，双单位口径）
    added_px = int(added.sum())
    removed_px = int(removed.sum())
    before_px = int(before.sum())
    after_px = int(after.sum())
    added_m2 = removed_m2 = None
    if geo_transform is not None:
        added_m2 = _area_m2(added, geo_transform)
        removed_m2 = _area_m2(removed, geo_transform)
    return diff_mask, DiffStatistics(
        added_px=added_px,
        removed_px=removed_px,
        net_change_px=after_px - before_px,
        change_rate=_change_rate(before_px, after_px),
        added_m2=added_m2,
        removed_m2=removed_m2,
    )


def _change_rate(before_px: int, after_px: int) -> float:
    """变化率 (after-before)/before；before=0 时定义 0（无变化）/1（从无到有）。"""
    if before_px == 0:
        return 0.0 if after_px == 0 else 1.0
    return (after_px - before_px) / before_px


def _remove_small_regions(mask: np.ndarray, min_area: int) -> np.ndarray:
    """连通域过滤（FR-7.4）；compute 侧本地实现（服务边界，不跨服务引用 infer）。"""
    mask = (mask > 0).astype(np.uint8)
    if min_area <= 0 or not mask.any():
        return mask
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros(num, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    return keep[labels].astype(np.uint8)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    if not (isinstance(color, str) and len(color) == 7 and color.startswith("#")):
        raise CvError("INVALID_INPUT", f"非法颜色值 {color!r}，须为 #RRGGBB", 400)
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _colorize_diff(added: np.ndarray, removed: np.ndarray, colors: DiffColors) -> np.ndarray:
    """差异蒙版：added/removed 分色、不变区域透明（FR-7.3）。"""
    h, w = added.shape
    canvas = np.zeros((h, w, 4), dtype=np.uint8)
    canvas[removed > 0] = (*_hex_to_rgb(colors.removed), 255)
    canvas[added > 0] = (*_hex_to_rgb(colors.added), 255)  # 同像素两态互斥，次序无影响
    return canvas


def _area_m2(mask: np.ndarray, geo_transform: list[float]) -> float:
    """地理面积：经纬度网格像素面积仅随纬度变化，逐行积分（CGCS2000 椭球）。"""
    rows = np.nonzero(mask.any(axis=1))[0]
    return float(sum(pixel_area_m2(geo_transform, int(r), 0) * int(mask[r].sum()) for r in rows))
