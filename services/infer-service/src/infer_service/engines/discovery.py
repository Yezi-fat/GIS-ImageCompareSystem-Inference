"""模型发现（bug-2026-09-28 Function-Q2/Q3 解决方案，Q2-P1/Q3-P1）。

扫描 MODELS__DIR 下 {name}/{version}/ 目录，产出推理服务实际可用模型清单
（GET /infer/models 数据源）：
- 双产物齐全性（fp32.onnx / int8.onnx，文件名取 L1 配置）；
- 类别表：读 ONNX 元数据 names（ultralytics 导出内嵌，索引=类别 ID）；
- 加载状态（ModelStore 会话缓存）与默认版本标记（L1 配置比对）。

ONNX 元数据读取同时供 ModelStore.effective_class_ids 自动推导类别表（Q2-P2）。
onnx 按仓库纪律函数内延迟导入。
"""
from __future__ import annotations

import ast
import os

import structlog

from cv_common.schemas.models import ModelEntry, ModelsResponse, ModelVersionInfo

from infer_service.config import Settings
from infer_service.engines.model_store import ModelStore

logger = structlog.get_logger(__name__)


def read_model_metadata(path: str) -> tuple[list[str] | None, int | None]:
    """读 ONNX 模型元数据 → (类别名称表, 输出维度数)；失败/缺失 → (None, None)。

    - names 为 ultralytics 导出内嵌的 str(dict)（如 "{0: 'AnnualCrop', ...}"），
      经 ast.literal_eval 解析后按键序排列，列表索引=类别 ID；
    - 输出维度数用于区分模型形态：4 维 [1,C,H,W]=逐像素分割概率图；
      3 维 [1,4+C,anchors]=检测框（YOLO 系，栅格化后末位通道为背景）。
    """
    try:
        import onnx

        model = onnx.load(path, load_external_data=False)
        names_raw = next((p.value for p in model.metadata_props if p.key == "names"), None)
        labels: list[str] | None = None
        if names_raw:
            names = ast.literal_eval(names_raw)
            if isinstance(names, dict) and names:
                labels = [names[k] for k in sorted(names, key=int)]
        output_ndim = None
        if model.graph.output:
            output_ndim = len(model.graph.output[0].type.tensor_type.shape.dim)
        return labels, output_ndim
    except Exception as e:
        logger.warning("model_metadata_read_failed", path=path, error=f"{type(e).__name__}: {e}")
        return None, None


def scan_models(settings: Settings, store: ModelStore) -> ModelsResponse:
    """扫描模型目录 → 模型清单（/infer/models 响应）。"""
    root = settings.models.dir
    seg_cfg = settings.models.segmentation
    loaded = store.loaded_models()
    entries: list[ModelEntry] = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            name_dir = os.path.join(root, name)
            if not os.path.isdir(name_dir):
                continue
            versions: list[ModelVersionInfo] = []
            for version in sorted(os.listdir(name_dir)):
                version_dir = os.path.join(name_dir, version)
                if not os.path.isdir(version_dir):
                    continue
                versions.append(_version_info(settings, name, version, version_dir, loaded))
            if versions:
                entries.append(ModelEntry(name=name, versions=versions))
    return ModelsResponse(models_dir=root, models=entries)


def _version_info(
    settings: Settings,
    name: str,
    version: str,
    version_dir: str,
    loaded: dict,
) -> ModelVersionInfo:
    """单版本发现信息：产物齐全性 + 类别表（fp32 优先，缺则 int8）+ 加载/默认标记。"""
    seg_cfg = settings.models.segmentation
    fp32_path = os.path.join(version_dir, seg_cfg.fp32)
    int8_path = os.path.join(version_dir, seg_cfg.int8)
    has_fp32, has_int8 = os.path.isfile(fp32_path), os.path.isfile(int8_path)

    labels: list[str] | None = None
    output_ndim: int | None = None
    meta_path = fp32_path if has_fp32 else (int8_path if has_int8 else None)
    if meta_path:
        labels, output_ndim = read_model_metadata(meta_path)
    class_count = _class_count(labels, output_ndim)

    default_for = [
        model_type
        for model_type in ("segmentation", "change_detection")
        if (name, version)
        == (getattr(settings.models, model_type).name, getattr(settings.models, model_type).version)
    ]
    is_loaded = any(k[1] == name and k[2] == version for k in loaded)
    return ModelVersionInfo(
        version=version,
        fp32=has_fp32,
        int8=has_int8,
        class_labels=labels,
        class_count=class_count,
        loaded=is_loaded,
        default_for=default_for,
    )


def _class_count(labels: list[str] | None, output_ndim: int | None) -> int | None:
    """概率图通道数：检测模型（3 维输出）= labels+1（末位背景）；分割模型（4 维）= labels。"""
    if labels is None:
        return None
    return len(labels) + 1 if output_ndim == 3 else len(labels)
