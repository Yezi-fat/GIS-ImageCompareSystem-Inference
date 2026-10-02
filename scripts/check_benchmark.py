#!/usr/bin/env python3
"""P-026 性能回归检查：当前基准结果 vs 基线，均值劣化 >20% 报警（退出码 1）。

用法：
  # 录制基线（入库 benchmarks/baseline.json）：
  .venv/bin/pytest services/infer-service/tests/test_benchmark.py \
      --benchmark-only --benchmark-json=benchmarks/baseline.json
  # CI 回归检查：
  .venv/bin/pytest services/infer-service/tests/test_benchmark.py \
      --benchmark-only --benchmark-json=/tmp/current.json
  .venv/bin/python scripts/check_benchmark.py benchmarks/baseline.json /tmp/current.json
"""
from __future__ import annotations

import json
import sys

THRESHOLD = 1.20  # 均值劣化 >20% 报警（清单 P-026 验收口径）


def _means(path: str) -> dict[str, float]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {b["name"]: b["stats"]["mean"] for b in data.get("benchmarks", [])}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    baseline, current = _means(sys.argv[1]), _means(sys.argv[2])
    failed = False
    for name, base_mean in baseline.items():
        if name not in current:
            print(f"[warn] 基线用例 {name} 本次未运行")
            continue
        cur_mean = current[name]
        ratio = cur_mean / base_mean if base_mean > 0 else 1.0
        status = "OK" if ratio <= THRESHOLD else "REGRESSED"
        print(f"[{status}] {name}: 基线 {base_mean*1000:.1f}ms → 本次 {cur_mean*1000:.1f}ms（{ratio:.2f}x）")
        if ratio > THRESHOLD:
            failed = True
    if failed:
        print("性能回归劣化 >20%，请排查（P-026）")
        return 1
    print("无显著回归")
    return 0


if __name__ == "__main__":
    sys.exit(main())
