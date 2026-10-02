"""蒙版着色（设计 §3.2.4，FR-6.3/7.3，P-011 实现）。

要素合成蒙版：RGBA 画布，按请求中的类别颜色表（Java 从 element_catalog 下发）
着色，非覆盖区域 alpha=0；各类别独立二值蒙版由编排层直接输出。
"""
from __future__ import annotations

import re

import numpy as np

from cv_common.errors import CvError

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def hex_to_rgba(color: str) -> tuple[int, int, int, int]:
    """'#RRGGBB' → (R, G, B, 255)。非法格式抛 CvError(INVALID_INPUT)。"""
    if not _HEX_COLOR.match(color):
        raise CvError("INVALID_INPUT", f"非法颜色值 {color!r}，须为 #RRGGBB", 400)
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16), 255


def colorize_combined(per_class_masks: dict[str, np.ndarray], colors: dict[str, str]) -> np.ndarray:
    """各类别二值蒙版 → 按类别着色的 RGBA 合成蒙版（非覆盖区域全透明，FR-6.3）。

    类别间重叠时后写类别覆盖先写类别（前端另有各类独立蒙版可开关查看）。
    """
    first = next(iter(per_class_masks.values()), None)
    if first is None:
        raise CvError("INVALID_INPUT", "per_class_masks 为空，无法合成", 400)
    h, w = first.shape[:2]
    canvas = np.zeros((h, w, 4), dtype=np.uint8)
    for element, mask in per_class_masks.items():
        r, g, b, a = hex_to_rgba(colors[element])
        covered = mask > 0
        canvas[covered] = (r, g, b, a)
    return canvas
