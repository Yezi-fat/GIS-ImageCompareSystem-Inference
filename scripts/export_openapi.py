#!/usr/bin/env python3
"""导出三服务 OpenAPI 契约快照至 docs/openapi/（P-008）。

快照是 Java 侧 CI 契约 diff 的事实源（设计 §8 契约测试）；
接口变更后须重新执行本脚本（全局要求 #2）。

用法：python3 scripts/export_openapi.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "openapi"

# 服务名 → (包名, app 路径, 快照文件)
SERVICES = {
    "infer": "infer_service.main:app",
    "compute": "compute_service.main:app",
    "nlp": "nlp_service.main:app",
}


def export_all() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, app_path in SERVICES.items():
        module_path, attr = app_path.split(":")
        try:
            module = __import__(module_path, fromlist=[attr])
            app = getattr(module, attr)
        except ImportError as e:
            print(f"[skip] {name}: {e}（请先 pip install -e 各服务包）")
            return 1
        spec = app.openapi()
        out = OUT_DIR / f"{name}.json"
        out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[ok] {name} → {out.relative_to(ROOT)}（{len(spec.get('paths', {}))} 条路径）")
    return 0


if __name__ == "__main__":
    sys.exit(export_all())
