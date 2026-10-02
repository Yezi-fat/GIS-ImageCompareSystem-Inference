"""模型发现接口响应模型（bug-2026-09-28 Function-Q2/Q3 解决方案，Q2-P1/Q3-P1）。

`GET /infer/models`：扫描推理服务模型目录（{name}/{version}/{fp32,int8}.onnx），
上报实际可用模型清单——双产物齐全性、类别表（读 ONNX 元数据 names，索引=类别 ID）、
加载状态、是否当前默认版本。供 Java 模型管理页展示/对账与 element_catalog 映射校验。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ModelVersionInfo(BaseModel):
    """单模型单版本的发现信息。"""

    version: str = Field(description="版本号（目录名）")
    fp32: bool = Field(description="fp32.onnx 产物是否存在（GPU 路径必需）")
    int8: bool = Field(description="int8.onnx 产物是否存在（CPU 路径必需）")
    class_labels: list[str] | None = Field(
        default=None,
        description="类别名称表（ONNX 元数据 names，列表索引=类别 ID）；模型未内嵌 names 时为 null",
    )
    class_count: int | None = Field(
        default=None,
        description="概率图通道数：检测模型（YOLO 系）= len(class_labels)+1（末位为背景通道）；"
                    "逐像素分割模型 = len(class_labels)；无 names 时为 null",
    )
    loaded: bool = Field(description="会话是否已加载常驻（ModelStore 缓存命中）")
    default_for: list[str] = Field(
        default_factory=list,
        description="该版本作为默认版本的模型类型：segmentation / change_detection（可兼得，通常至多其一）",
    )


class ModelEntry(BaseModel):
    """单模型（按名聚合各版本）。"""

    name: str = Field(description="模型名（目录名），如 landcover-seg")
    versions: list[ModelVersionInfo]


class ModelsResponse(BaseModel):
    """GET /infer/models 响应。"""

    models_dir: str = Field(description="模型目录（MODELS__DIR 实值）")
    models: list[ModelEntry]
