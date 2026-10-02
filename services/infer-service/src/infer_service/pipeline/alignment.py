"""配准校验（设计 §3.2.5，FR-1.2/7.7 告警路径，评审 D-03，P-018a 实现）。

本期能力范围：几何偏差**校验告警**——双图整体平移估计采用相位相关
（cv2.phaseCorrelate，灰度化 + 降采样至长边 ≤1024 后计算，CPU 毫秒级）；
自动配准（重采样对齐）列 M6（需求第 12 章）。

适用链路：/infer/change-detection（双图同请求到达）编排内置、推理前执行；
双期比对/多期时序链路两期影像分请求到达本服务，像素级平移估计需引入配对机制，
本期由 Java 侧 geo_extent 一致性校验（FR-8.7）兜底范围级偏差。
"""
from __future__ import annotations

import numpy as np

from cv_common.errors import AlignmentFailedError, GeoExtentMismatchError

# 相位相关前降采样的长边上限（设计 §3.2.5）
_DOWNSCALE_MAX_SIDE = 1024


def estimate_shift(before: np.ndarray, after: np.ndarray) -> float:
    """相位相关整体平移估计 → 平移量（两轴取大，换算回原分辨率像素）。

    灰度化 + Hanning 窗抑制边缘效应；双图尺寸不一致 → GeoExtentMismatchError；
    低纹理图象估计失效（非有限值）时按 0 处理（无证据不告警）。
    """
    import cv2

    if before.shape[:2] != after.shape[:2]:
        raise GeoExtentMismatchError(
            f"双期影像尺寸不一致：{before.shape[:2]} vs {after.shape[:2]}（须同源同层级瓦片）"
        )
    gray_b = cv2.cvtColor(before, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gray_a = cv2.cvtColor(after, cv2.COLOR_RGB2GRAY).astype(np.float32)

    h, w = gray_b.shape
    scale = min(1.0, _DOWNSCALE_MAX_SIDE / max(h, w))
    if scale < 1.0:
        gray_b = cv2.resize(gray_b, None, fx=scale, fy=scale)
        gray_a = cv2.resize(gray_a, None, fx=scale, fy=scale)

    win = cv2.createHanningWindow((gray_b.shape[1], gray_b.shape[0]), cv2.CV_32F)
    (dx, dy), _response = cv2.phaseCorrelate(gray_b, gray_a, win)
    shift = max(abs(dx), abs(dy)) / scale
    return float(shift) if np.isfinite(shift) else 0.0


def check_alignment(before: np.ndarray, after: np.ndarray, max_shift_px: int = 4) -> float:
    """配准校验：返回实测平移量；超阈值抛 AlignmentFailedError（ALIGNMENT_FAILED/422）。

    message 注明“自动配准能力本期未启用（M6 交付）”——不静默忽略（§3.2.5）。
    auto_align 参数由编排层透传记录（响应 auto_aligned=false），不改本流程。
    """
    shift = estimate_shift(before, after)
    if shift > max_shift_px:
        raise AlignmentFailedError(
            f"双期影像整体平移偏差 {shift:.1f}px 超阈值 {max_shift_px}px，存在配准风险；"
            f"自动配准能力本期未启用（M6 交付），请确认两期影像同源同层级后重试"
        )
    return shift
