#!/usr/bin/env python3
"""伪 ONNX 模型制作脚本（P-012；设计 §9 交付物，M2 联调主线）。

真实模型交付前，用恒等/确定性输出的伪模型跑通 infer-service 全管线
（loader→tiler→engine→postprocess→colorize→统计）。

生成两类伪模型（动态 H/W 输入，FP32/INT8 同名落两份——伪模型不做真量化）：
- landcover-seg/v0.0-fake/    输入 input[1,3,H,W] → 输出 prob[1,5,H,W]
    各类别 logit = [128, G, (R+G)/2, mean(RGB), R]（类别顺序 0=背景/1=forest/2=grassland/
    3=snow/4=building），经 Softmax 归一：绿色像素→forest、红色像素→building，
    输出确定性可断言；
- change-detection/v0.0-fake/ 输入 before/after[1,3,H,W] → 输出 prob[1,1,H,W]
    输出 = 双时相 RGB 均值差的绝对值归一（端到端变化检测的最小可测语义）。

用法：python3 scripts/make_fake_models.py [--out services/infer-service/models]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import onnx
from onnx import helper, numpy_helper


def _make_segmentation() -> onnx.ModelProto:
    """input[1,3,H,W]（0~1 归一化）→ prob[1,5,H,W]：logit=[128, G, (R+G)/2, mean, R] → Softmax。

    内部先将输入 ×255 还原到像素量级，与背景 logit 常数 128 可比。
    """
    inp = helper.make_tensor_value_info("input", onnx.TensorProto.FLOAT, [1, 3, "H", "W"])
    out = helper.make_tensor_value_info("prob", onnx.TensorProto.FLOAT, [1, 5, "H", "W"])

    consts = {
        "idx_r": np.array([0], dtype=np.int64),
        "idx_g": np.array([1], dtype=np.int64),
        "c255": np.array(255.0, dtype=np.float32),
        "zero": np.array(0.0, dtype=np.float32),
        "c128": np.array(128.0, dtype=np.float32),
        "half": np.array(0.5, dtype=np.float32),
    }
    nodes = [
        helper.make_node("Mul", ["input", "c255"], ["scaled"]),          # 0~1 → 0~255
        helper.make_node("Gather", ["scaled", "idx_r"], ["r"], axis=1),  # [1,1,H,W]
        helper.make_node("Gather", ["scaled", "idx_g"], ["g"], axis=1),  # [1,1,H,W]
        helper.make_node("ReduceMean", ["scaled"], ["mean"], axes=[1], keepdims=1),
        helper.make_node("Mul", ["mean", "zero"], ["zero_map"]),
        helper.make_node("Add", ["zero_map", "c128"], ["logit0"]),       # 背景
        helper.make_node("Add", ["g", "r"], ["g_plus_r"]),
        helper.make_node("Mul", ["g_plus_r", "half"], ["logit2"]),       # 草地
        # 类别顺序 [0=背景, 1=forest(G), 2=grassland((R+G)/2), 3=snow(mean), 4=building(R)]
        helper.make_node("Concat", ["logit0", "g", "logit2", "mean", "r"], ["logits"], axis=1),
        helper.make_node("Softmax", ["logits"], ["prob"], axis=1),
    ]
    graph = helper.make_graph(
        nodes, "fake-segmentation", [inp], [out],
        initializer=[numpy_helper.from_array(v, k) for k, v in consts.items()],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    # 内嵌类别名称表（Q2-P2：class_ids 缺省时从 names 自动推导；4 维输出 → [0..4]）
    meta = model.metadata_props.add()
    meta.key = "names"
    meta.value = str({0: "background", 1: "forest", 2: "grassland", 3: "snow", 4: "building"})
    return model


def _make_change_detection() -> onnx.ModelProto:
    """before/after[1,3,H,W] → prob[1,1,H,W]：|mean(after)-mean(before)|。"""
    before = helper.make_tensor_value_info("before", onnx.TensorProto.FLOAT, [1, 3, "H", "W"])
    after = helper.make_tensor_value_info("after", onnx.TensorProto.FLOAT, [1, 3, "H", "W"])
    out = helper.make_tensor_value_info("prob", onnx.TensorProto.FLOAT, [1, 1, "H", "W"])

    nodes = [
        helper.make_node("ReduceMean", ["before"], ["mb"], axes=[1], keepdims=1),
        helper.make_node("ReduceMean", ["after"], ["ma"], axes=[1], keepdims=1),
        helper.make_node("Sub", ["ma", "mb"], ["diff"]),
        helper.make_node("Abs", ["diff"], ["prob"]),
    ]
    graph = helper.make_graph(nodes, "fake-change-detection", [before, after], [out], initializer=[])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="services/infer-service/models", help="模型根目录（{name}/{version}/ 组织）")
    args = parser.parse_args()
    root = Path(args.out)

    specs = {
        ("landcover-seg", "v0.0-fake"): _make_segmentation(),
        ("change-detection", "v0.0-fake"): _make_change_detection(),
    }
    for (name, version), model in specs.items():
        onnx.checker.check_model(model)
        dest = root / name / version
        dest.mkdir(parents=True, exist_ok=True)
        for precision in ("fp32", "int8"):  # 伪模型两份同内容（真量化属 P-029）
            path = dest / f"{precision}.onnx"
            onnx.save(model, path)
            print(f"[ok] {path}")
    print("伪模型生成完成（仅用于无真实模型时联调，真实模型交付后由 P-014 收口）")


if __name__ == "__main__":
    main()
