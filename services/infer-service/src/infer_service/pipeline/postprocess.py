"""后处理（设计 §3.2.3，FR-1.6/7.4，P-011 实现）：形态学去噪 + 小区域过滤。"""
from __future__ import annotations

import cv2
import numpy as np

_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def postprocess_mask(mask: np.ndarray, min_area: int) -> np.ndarray:
    """形态学开运算去噪点 + 闭运算连碎片 + 连通域小区域过滤（阈值可配置）。"""
    mask = (mask > 0).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _KERNEL)  # 去噪点
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _KERNEL)  # 连碎片
    return remove_small_regions(mask, min_area)


def remove_small_regions(mask: np.ndarray, min_area: int) -> np.ndarray:
    """连通域过滤：面积 < min_area 的区域置零（FR-1.6/7.4）。"""
    mask = (mask > 0).astype(np.uint8)
    if min_area <= 0 or mask.size == 0 or not mask.any():
        return mask
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros(num, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
    return keep[labels].astype(np.uint8)
