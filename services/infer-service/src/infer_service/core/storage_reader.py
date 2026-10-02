"""对象存储影像读取（设计 §2.3 storage_reader，评审 P-03，P-010 实现）。

按 Java 签发的内网预签名 URL 直接拉取影像（httpx）——
Python 侧零配置、零内部令牌；URL 过期/失效抛 ImageDecodeFailedError，
message 携带存储端 HTTP 状态供 Java 判定重签（J-02）。
"""
from __future__ import annotations

import httpx

from cv_common.errors import ImageDecodeFailedError


def fetch_bytes(url: str, timeout_s: int = 30) -> bytes:
    """按预签名 URL 拉取影像字节流。

    - 连接失败/超时 → ImageDecodeFailedError（网络层原因）；
    - 存储端非 2xx（如 403 签名过期）→ ImageDecodeFailedError，
      message 携带 HTTP 状态码，供 Java 判定重签后重试（J-02）。
    """
    try:
        resp = httpx.get(url, timeout=timeout_s, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ImageDecodeFailedError(f"影像拉取失败（网络层）：{type(e).__name__}: {e}") from e
    if resp.status_code != 200:
        raise ImageDecodeFailedError(
            f"影像拉取失败：存储端返回 HTTP {resp.status_code}"
            f"（{'403 多为预签名 URL 过期，可重签重试' if resp.status_code == 403 else '请检查 URL 有效性'}）"
        )
    return resp.content
