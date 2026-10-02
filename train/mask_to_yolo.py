#!/usr/bin/env python3
"""语义分割掩膜数据集 → YOLO 检测标注转换（train/README.md §2.2）。

LoveDA / LandCover.ai / DeepGlobe 等数据集给的是逐像素类别掩膜 PNG，
本脚本对每类取连通区域外接框（过滤小斑块），生成 YOLO 格式标注并划分 train/val。

用法：
  python train/mask_to_yolo.py \
    --images /data/loveda/images --masks /data/loveda/masks \
    --mask-mapping '{"1":0,"2":1,"3":2,"4":3}' \
    --out train/dataset --val-ratio 0.1 --min-area-px 64

--mask-mapping：掩膜像素值 → 本模型类别序号（0=forest 1=grassland 2=snow 3=building），
按所用数据集的实际调色板/类别表填写；掩膜中未映射的像素值忽略（视为背景）。
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

import numpy as np

IMG_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def mask_to_boxes(mask: np.ndarray, mapping: dict[int, int], min_area: int) -> list[tuple[int, int, int, int, int]]:
    """单张掩膜 → [(cls, x1, y1, x2, y2)]：每类连通区域外接框（8 连通）。"""
    import cv2

    boxes: list[tuple[int, int, int, int, int]] = []
    for src_value, cls in mapping.items():
        binary = (mask == src_value).astype(np.uint8)
        num, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        for i in range(1, num):
            x, y, w, h, area = stats[i]
            if area >= min_area:
                boxes.append((cls, int(x), int(y), int(x + w), int(y + h)))
    return boxes


def find_mask(masks_dir: Path, stem: str) -> Path | None:
    for ext in IMG_EXTS:
        p = masks_dir / f"{stem}{ext}"
        if p.is_file():
            return p
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", required=True, help="原图目录")
    parser.add_argument("--masks", required=True, help="掩膜目录（与原图同 stem）")
    parser.add_argument("--mask-mapping", required=True,
                        help='掩膜像素值→类别序号 JSON，如 {"1":0,"2":1,"3":2,"4":3}')
    parser.add_argument("--out", default="train/dataset", help="输出数据集根目录")
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--min-area-px", type=int, default=64, help="连通区域最小面积（像素）")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import cv2

    mapping = {int(k): int(v) for k, v in json.loads(args.mask_mapping).items()}
    images_dir, masks_dir = Path(args.images), Path(args.masks)
    out = Path(args.out)

    images = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in IMG_EXTS)
    if not images:
        raise SystemExit(f"[error] 原图目录为空：{images_dir}")
    rng = random.Random(args.seed)
    rng.shuffle(images)
    val_count = max(1, int(len(images) * args.val_ratio))
    splits = {"val": images[:val_count], "train": images[val_count:]}

    total_boxes = 0
    for split, files in splits.items():
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        for img_path in files:
            mask_path = find_mask(masks_dir, img_path.stem)
            if mask_path is None:
                print(f"[skip] 无对应掩膜：{img_path.name}")
                continue
            img = cv2.imread(str(img_path))
            mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
            if img is None or mask is None:
                print(f"[skip] 解码失败：{img_path.name}")
                continue
            h, w = mask.shape
            lines = []
            for cls, x1, y1, x2, y2 in mask_to_boxes(mask, mapping, args.min_area_px):
                cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
                bw, bh = (x2 - x1) / w, (y2 - y1) / h
                lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            total_boxes += len(lines)
            suffix = ".jpg" if img_path.suffix.lower() in {".jpg", ".jpeg"} else ".png"
            cv2.imwrite(str(out / "images" / split / f"{img_path.stem}{suffix}"), img)
            (out / "labels" / split / f"{img_path.stem}.txt").write_text("\n".join(lines))
        print(f"[ok] {split}: {len(files)} 图")

    print(f"[done] 共 {total_boxes} 个标注框 → {out}")
    print("下一步：python train/train.py --data train/data.yaml --weights yolo11s.pt --epochs 100")


if __name__ == "__main__":
    main()
