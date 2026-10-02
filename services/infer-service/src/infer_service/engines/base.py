"""推理引擎统一抽象（设计 §3.1.1，FR-5.1）。

实现里程碑：P-012（Protocol 落地）。
签名说明：segment/detect_change 的可选 model_name/model_version 承载
FR-3.5 按请求多版本选择（评审 P-02；M1 骨架签名扩展，设计 §2.3 已同步）。
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

from cv_common.schemas.health import ModelInfo


class InferenceEngine(Protocol):
    """两类模型（语义分割、变化检测）统一推理接口（FR-5.1）。"""

    def segment(
        self,
        tiles: list[np.ndarray],
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> list[np.ndarray]:
        """语义分割：输入预处理后的 Tile 批次，返回各类别概率图 [C,H,W]。

        model_name/model_version 缺省用 L1 配置默认版本（FR-3.5 切换链路）。
        """
        ...

    def detect_change(
        self,
        tiles_before: list[np.ndarray],
        tiles_after: list[np.ndarray],
        model_name: str | None = None,
        model_version: str | None = None,
    ) -> list[np.ndarray]:
        """变化检测：输入双时相 Tile 批次，返回变化概率图 [1,H,W]。"""
        ...

    def info(self) -> ModelInfo:
        """模型名、版本、provider、支持类别 ID 列表（FR-3.3 追溯数据源）。"""
        ...
