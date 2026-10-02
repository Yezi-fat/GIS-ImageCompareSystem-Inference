#!/usr/bin/env python3
"""INT8 量化脚本（P-029，FR-3.2）：FP32 ONNX → INT8 动态量化，附精度损失报告。

模型交付流水线（需求 14.7）：每模型交付 FP32 + INT8 两份产物，
按 {name}/{version}/{fp32,int8}.onnx 组织（评审 P-02/J-05）。

用法：
  .venv/bin/python scripts/quantize_int8.py <fp32.onnx> [输出目录]
  # 例：.venv/bin/python scripts/quantize_int8.py models/landcover-seg/v2.0/fp32.onnx

产出：同目录 int8.onnx + 精度对比报告（逐输出最大/均方误差，验收口径 ≤2% 精度损失）。
真实模型的精度损失须在业务测试集上评测（需求 8.2），本脚本报告为输出张量级比对。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


def quantize(fp32_path: Path) -> Path:
    """动态量化 FP32 → INT8（onnxruntime quantize_dynamic，无需校准集）。"""
    from onnxruntime.quantization import QuantType, quantize_dynamic

    int8_path = fp32_path.with_name("int8.onnx")
    quantize_dynamic(
        model_input=str(fp32_path),
        model_output=str(int8_path),
        weight_type=QuantType.QInt8,
    )
    return int8_path


def compare(fp32_path: Path, int8_path: Path) -> list[dict]:
    """随机输入下两模型输出比对 → 逐输出 {max_abs_err, rmse, rel_err_pct}。"""
    import onnxruntime as ort

    rng = np.random.default_rng(42)
    sess_fp32 = ort.InferenceSession(str(fp32_path), providers=["CPUExecutionProvider"])
    sess_int8 = ort.InferenceSession(str(int8_path), providers=["CPUExecutionProvider"])

    inputs = {
        i.name: rng.random(
            [d if isinstance(d, int) else 256 for d in i.shape], dtype=np.float32
        )
        for i in sess_fp32.get_inputs()
    }
    out_fp32 = sess_fp32.run(None, inputs)
    out_int8 = sess_int8.run(None, inputs)

    report = []
    for name, a, b in zip((o.name for o in sess_fp32.get_outputs()), out_fp32, out_int8, strict=True):
        a64, b64 = a.astype(np.float64), b.astype(np.float64)
        max_abs = float(np.max(np.abs(a64 - b64)))
        rmse = float(np.sqrt(np.mean((a64 - b64) ** 2)))
        ref = float(np.sqrt(np.mean(a64**2))) or 1.0
        report.append({
            "output": name,
            "max_abs_err": max_abs,
            "rmse": rmse,
            "rel_err_pct": rmse / ref * 100,
        })
    return report


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    fp32_path = Path(sys.argv[1])
    if not fp32_path.is_file():
        print(f"[error] 模型文件不存在：{fp32_path}")
        return 2

    int8_path = quantize(fp32_path)
    size_fp32 = fp32_path.stat().st_size / 1e6
    size_int8 = int8_path.stat().st_size / 1e6
    print(f"[ok] 量化产物：{int8_path}（{size_fp32:.1f}MB → {size_int8:.1f}MB，压缩 {size_fp32/size_int8:.1f}x）")

    report = compare(fp32_path, int8_path)
    worst = max(r["rel_err_pct"] for r in report)
    for row in report:
        print(f"  输出 {row['output']}: max_abs_err={row['max_abs_err']:.6f} "
              f"rmse={row['rmse']:.6f} rel_err={row['rel_err_pct']:.3f}%")
    # 验收口径：INT8 相对 FP32 精度损失 ≤2 个百分点（需求 8.2；张量级 RMSE 相对误差）
    status = "通过" if worst <= 2.0 else "超阈值——请在业务测试集复核（需求 8.2）"
    print(f"[report] 最大相对误差 {worst:.3f}%（阈值 ≤2%）：{status}")
    return 0 if worst <= 2.0 else 1


if __name__ == "__main__":
    sys.exit(main())
