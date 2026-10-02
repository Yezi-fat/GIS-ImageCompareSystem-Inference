#!/usr/bin/env python3
"""YOLO11 影像地图要素检测训练（train/README.md §3）。

用法：
  python train/train.py --data train/data.yaml --weights yolo11s.pt \
    --epochs 100 --imgsz 256 --batch 16 --device 0

要点：
- --imgsz 默认 256，与推理侧 Tile 尺寸一致（训练/推理同尺度）；
- 区域型要素建议关闭 mosaic 末段以外的强几何增广之外的默认配置即可，
  遥感影像翻转/旋转不改变语义（YOLO 默认 fliplr=0.5 可用）；
- 训练需要 GPU；CPU 仅适合 --epochs 1~3 的管线冒烟。
"""
from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="train/data.yaml", help="数据集配置 yaml")
    parser.add_argument("--weights", default="yolo11s.pt", help="预训练权重（yolo11n/s/m/l/x.pt）")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=256, help="训练尺寸（与推理 Tile 256 对齐）")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default="0", help="GPU 序号；CPU 冒烟用 cpu")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--name", default="landcover-yolo11s", help="实验名（train/runs/<name>）")
    parser.add_argument("--patience", type=int, default=20, help="早停轮数")
    args = parser.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        project="train/runs",
        name=args.name,
        patience=args.patience,
        # 区域型地物：关闭 mosaic 末期之外的过强增广无必要，但 copy_paste 不适合
        # 连通区域语义（会把森林块贴到雪山上），显式关闭
        copy_paste=0.0,
        # 余弦学习率 + 默认 mosaic/h sv 增广即可
        cos_lr=True,
    )

    metrics = model.val(data=args.data, imgsz=args.imgsz, device=args.device)
    print(f"[ok] 训练完成，权重：train/runs/{args.name}/weights/best.pt")
    print(f"[val] mAP50-95={metrics.box.map:.4f} mAP50={metrics.box.map50:.4f}")
    # 每类指标（数据不平衡定位用，README §3）
    for i, name in enumerate(metrics.names.values()):
        print(f"  class {i} {name}: P={metrics.box.p[i]:.3f} R={metrics.box.r[i]:.3f} "
              f"mAP50={metrics.box.ap50[i]:.3f}")


if __name__ == "__main__":
    main()
