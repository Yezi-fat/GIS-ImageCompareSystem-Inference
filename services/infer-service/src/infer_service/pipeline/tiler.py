"""Tile 切分与重叠融合拼接（设计 §3.2.2，FR-1.3/6.7，P-011 实现）。

大图内存控制：流式处理——切一块、推一块、写回结果画布一块，
不在内存同时持有全部 Tile（设计 §5）。

融合口径：canvas 采用“概率和画布 + 计数画布”双缓冲，
fuse_tile 原地累加，finalize_canvas 取均值——重叠区均值融合，避免接缝（FR-6.7）。
"""
from __future__ import annotations

from typing import Iterator

import numpy as np


def split_tiles(
    image: np.ndarray,
    tile_size: int = 256,
    overlap: int = 32,
) -> Iterator[tuple[np.ndarray, tuple[int, int]]]:
    """流式切分（stride=tile_size-overlap），yield (tile, (y_offset, x_offset))。

    边缘 Tile 回缩对齐图像右/下边界（尺寸可能小于 tile_size，推理侧需支持
    动态尺寸或自行 padding——伪/真实模型均为动态 H/W 输入）。
    """
    h, w = image.shape[:2]
    stride = tile_size - overlap
    if stride <= 0:
        raise ValueError(f"overlap({overlap}) 必须小于 tile_size({tile_size})")

    def _edges(length: int) -> list[int]:
        if length <= tile_size:
            return [0]
        starts = list(range(0, length - tile_size + 1, stride))
        if starts[-1] != length - tile_size:
            starts.append(length - tile_size)
        return starts

    for y in _edges(h):
        for x in _edges(w):
            yield image[y : y + tile_size, x : x + tile_size], (y, x)


def alloc_canvas(height: int, width: int, channels: int) -> tuple[np.ndarray, np.ndarray]:
    """预分配结果画布：(概率和画布 float32[C,H,W], 计数画布 float32[1,H,W])。"""
    return (
        np.zeros((channels, height, width), dtype=np.float32),
        np.zeros((1, height, width), dtype=np.float32),
    )


def fuse_tile(
    canvas_prob: np.ndarray,
    tile_prob: np.ndarray,
    offset: tuple[int, int],
    count_canvas: np.ndarray | None = None,
) -> None:
    """重叠区概率图累加融合（配合 count_canvas 实现均值，避免接缝，FR-6.7）。

    canvas_prob/count_canvas 为 alloc_canvas 预分配的双缓冲，原地写回；
    count_canvas 缺省时直接覆盖写入（调用方保证无重叠场景）。
    """
    y, x = offset
    th, tw = tile_prob.shape[-2], tile_prob.shape[-1]
    region3 = (slice(None), slice(y, y + th), slice(x, x + tw))  # [C]/[1], H, W 三维切片
    if count_canvas is None:
        canvas_prob[region3] = tile_prob
        return
    canvas_prob[region3] += tile_prob
    count_canvas[region3] += 1.0


def finalize_canvas(canvas_prob: np.ndarray, count_canvas: np.ndarray) -> np.ndarray:
    """概率和画布 ÷ 计数画布 → 均值概率图 [C,H,W]（重叠区均值融合收口）。"""
    return canvas_prob / np.maximum(count_canvas, 1.0)
