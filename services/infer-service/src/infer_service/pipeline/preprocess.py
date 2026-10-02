"""预处理（设计 §2.3 preprocess）。

当前口径（P-014 已收口，yolo11 v1.0 交付规范）：uint8 → float32 /255 归一化 + HWC→CHW。
loader 已统一转 RGB（含 OpenCV BGR→RGB），与 YOLO 系模型训练口径一致；
逐像素概率图分割模型（含伪模型）同样适用该归一化。
"""
from __future__ import annotations

import numpy as np


def preprocess_tile(tile: np.ndarray) -> np.ndarray:
    """单 Tile 预处理：归一化 + HWC→CHW 通道变换 + float32。

    TODO(P-018)：缓冲池复用 numpy 数组减少 GC 压力（设计 §5）。
    """
    return np.ascontiguousarray(tile.transpose(2, 0, 1), dtype=np.float32) / 255.0
