#!/usr/bin/env python3
"""训练权重 → 部署双产物导出（train/README.md §4）。

流程：best.pt → fp32.onnx（动态 H/W，输出 [1, 4+C, anchors]，与 yolo_decode 契约匹配）
→ 调用仓库 scripts/quantize_int8.py 产 int8.onnx（含精度比对报告）
→ 按 {models}/{name}/{version}/{fp32,int8}.onnx 落位。

用法（仓库根目录执行）：
  python train/export_onnx.py --weights train/runs/landcover-yolo11s/weights/best.pt \
    --name landcover-seg --version v3.0
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", required=True, help="训练产物 best.pt 路径")
    parser.add_argument("--name", default="landcover-seg", help="模型名（目录名，Java 激活用）")
    parser.add_argument("--version", required=True, help="版本号（如 v3.0）")
    parser.add_argument("--imgsz", type=int, default=256, help="导出参考尺寸（动态轴，仅影响示例输入）")
    parser.add_argument("--models-dir", default=str(REPO_ROOT / "models"), help="模型根目录")
    args = parser.parse_args()

    weights = Path(args.weights)
    if not weights.is_file():
        raise SystemExit(f"[error] 权重不存在：{weights}")

    # ① 导出 ONNX（动态 H/W——推理侧按 256 Tile 输入，动态轴避免 letterbox 失真）
    from ultralytics import YOLO

    model = YOLO(str(weights))
    exported = Path(model.export(format="onnx", dynamic=True, imgsz=args.imgsz, opset=13))
    print(f"[ok] ONNX 导出：{exported}")

    dest = Path(args.models_dir) / args.name / args.version
    dest.mkdir(parents=True, exist_ok=True)
    fp32_path = dest / "fp32.onnx"
    shutil.move(str(exported), fp32_path)

    # ② INT8 量化（复用仓库脚本：动态量化 + 张量级精度比对，≤2% 口径）
    quantize = REPO_ROOT / "scripts" / "quantize_int8.py"
    python = REPO_ROOT / ".venv" / "bin" / "python"
    cmd = [str(python if python.is_file() else sys.executable), str(quantize), str(fp32_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print("[warn] 量化精度比对超阈值——请在业务测试集复测 mAP 后再上线（需求 8.2）")

    # ③ 落位核对 + 后续指引
    for f in ("fp32.onnx", "int8.onnx"):
        p = dest / f
        print(f"  {'✓' if p.is_file() else '✗'} {p}（{p.stat().st_size / 1e6:.1f}MB）" if p.is_file() else f"  ✗ 缺失：{p}")

    print(f"""
[下一步] 部署切换（详见 train/README.md §4）：
  1. deploy/.env：
       MODELS__SEGMENTATION__NAME={args.name}
       MODELS__SEGMENTATION__VERSION={args.version}
       MODELS__SEGMENTATION__CLASS_IDS=[0,1,2,3,4]   # 4 类 + 背景通道 4
     重启：cd deploy && docker compose up -d --force-recreate infer-service
  2. Java 侧：POST /api/v1/models 登记 + PUT /api/v1/models/active 激活
     要素目录 model_class_id → forest:0, grassland:1, snow:2, building:3
""")


if __name__ == "__main__":
    main()
