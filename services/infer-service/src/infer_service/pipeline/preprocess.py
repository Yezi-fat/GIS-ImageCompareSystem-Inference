"""预处理（设计 §2.3 preprocess）。

默认口径（P-014，YOLO 系与伪模型）：uint8 → float32 /255 归一化 + HWC→CHW。
层级路由低层级模型（SegFormer-RS，工作任务十一）按其训练口径支持
mean/std 归一化（HF SegFormer 惯例为 ImageNet 均值/标准差，L1 配置可调）。
loader 已统一转 RGB（含 OpenCV BGR→RGB），mean/std 配置按 RGB 序给出。
"""
from __future__ import annotations

import numpy as np


def preprocess_tile(
    tile: np.ndarray,
    mean: list[float] | None = None,
    std: list[float] | None = None,
) -> np.ndarray:
    """单 Tile 预处理：归一化 + HWC→CHW 通道变换 + float32。

    mean/std 缺省：仅 /255 归一化（YOLO 系与伪模型口径）；
    提供时：(x/255 - mean) / std（通道维广播，RGB 序，SegFormer 等 HF 模型口径）。

    TODO(P-018)：缓冲池复用 numpy 数组减少 GC 压力（设计 §5）。
    """
    x = tile.astype(np.float32) / 255.0
    if mean is not None and std is not None:
        x = (x - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    return np.ascontiguousarray(x.transpose(2, 0, 1))
