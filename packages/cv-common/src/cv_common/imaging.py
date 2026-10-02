"""影像编解码工具（设计 §2.2）：PNG base64 编解码、二值蒙版读写。

实现里程碑：P-009。cv2 函数内延迟导入——cv-common 安装不强制依赖 opencv。
"""
from __future__ import annotations

import base64

import numpy as np

from cv_common.errors import ImageDecodeFailedError


def decode_b64_png(b64: str) -> np.ndarray:
    """base64 PNG → numpy 数组（BGR/RGBA 视通道数；调用方按需转 RGB）。"""
    import cv2

    try:
        buf = base64.b64decode(b64, validate=True)
        arr = np.frombuffer(buf, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    except Exception as e:  # base64/解码异常统一收敛为契约错误
        raise ImageDecodeFailedError(f"base64 PNG 解码失败：{e}") from e
    if img is None:
        raise ImageDecodeFailedError("base64 PNG 解码失败：imdecode 返回空（数据非合法 PNG）")
    return img


def encode_png_b64(arr: np.ndarray) -> str:
    """numpy 数组 → base64 PNG（蒙版/概率图随响应回传 Java 的编码入口）。"""
    import cv2

    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise ImageDecodeFailedError("PNG 编码失败：imencode 返回失败")
    return base64.b64encode(buf.tobytes()).decode("ascii")
