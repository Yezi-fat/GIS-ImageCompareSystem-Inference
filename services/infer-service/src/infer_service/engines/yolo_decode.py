"""YOLO 系检测模型输出解码（P-014 真实模型接入：yolo11 v1.0）。

模型输出为检测框格式 [1, 4+C, anchors]（YOLOv8+：cx,cy,w,h + C 类置信度，
无 objectness），与分割模型的逐像素概率图 [1, C, H, W] 不同构。
本模块将检测框栅格化为逐像素概率图 [C+1, H, W]，统一引擎 segment() 契约：

- 通道 k（0..C-1）= COCO 类别 k 的置信度，填充于其检测框覆盖的像素
  （多框重叠取最大置信度）——**类别通道与 COCO 序号一一直接对应**；
- 通道 C（末位）= 背景：1 - 各类通道最大值（下界 0）；
- 因此下发 Java 侧的 class_mapping 约定：**类别值 = COCO 类别序号直用**，
  class_ids 配置为 [0..C]，其中 C 保留为背景、不可映射业务要素。

cv2 按仓库纪律函数内延迟导入。
"""
from __future__ import annotations

import numpy as np


def decode_yolo_detections(
    raw: np.ndarray,
    height: int,
    width: int,
    conf_threshold: float,
    nms_threshold: float,
) -> np.ndarray:
    """YOLO 检测输出（batch 已剥离，[4+C, anchors]）→ 概率图 [C+1, H, W]。

    流程：置信度过滤 → cxcywh 转 xyxy 并裁剪到 Tile 范围 → 逐类 NMS →
    检测框栅格化（通道=COCO 序号直用，重叠区取最大置信度）→ 末位背景通道取残差。
    """
    import cv2

    num_classes = raw.shape[0] - 4
    preds = raw.T                                    # [anchors, 4+C]
    class_scores = preds[:, 4:]
    class_ids = class_scores.argmax(axis=1)
    confs = class_scores[np.arange(len(preds)), class_ids]
    keep = confs >= conf_threshold

    background = num_classes                         # 背景通道 = 末位
    prob = np.zeros((num_classes + 1, height, width), dtype=np.float32)
    if not keep.any():
        prob[background] = 1.0
        return prob

    cx, cy, w, h = preds[keep, 0], preds[keep, 1], preds[keep, 2], preds[keep, 3]
    kept_cls, kept_conf = class_ids[keep], confs[keep]
    boxes_xywh = np.stack([cx - w / 2, cy - h / 2, w, h], axis=1)  # NMSBoxes 入参格式

    for cls in np.unique(kept_cls):
        idxs = np.nonzero(kept_cls == cls)[0]
        picked = cv2.dnn.NMSBoxes(
            boxes_xywh[idxs].tolist(), kept_conf[idxs].tolist(), conf_threshold, nms_threshold
        )
        for i in np.asarray(picked).reshape(-1):
            x1 = int(max(0.0, boxes_xywh[idxs[i]][0]))
            y1 = int(max(0.0, boxes_xywh[idxs[i]][1]))
            x2 = int(min(float(width), boxes_xywh[idxs[i]][0] + boxes_xywh[idxs[i]][2]))
            y2 = int(min(float(height), boxes_xywh[idxs[i]][1] + boxes_xywh[idxs[i]][3]))
            if x2 > x1 and y2 > y1:
                region = prob[cls, y1:y2, x1:x2]
                np.maximum(region, kept_conf[idxs[i]], out=region)

    prob[background] = np.clip(1.0 - prob[:background].max(axis=0), 0.0, 1.0)
    return prob
