"""infer-service 测试公共配置（全局要求 #5：合成小尺寸影像，不依赖真实模型）。

环境变量须先于 infer_service 导入设置（pydantic-settings 在 import 时实例化）：
- MODELS__DIR 指向仓库内伪模型目录（scripts/make_fake_models.py 生成）；
- 默认模型版本指向 v0.0-fake；
- AUTH_ENABLED=false 使 e2e 请求免令牌（auth 两态行为由 cv-common 测试覆盖）。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODELS_DIR = ROOT / "services" / "infer-service" / "models"

os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("MODELS__DIR", str(MODELS_DIR))
os.environ.setdefault("MODELS__SEGMENTATION__VERSION", "v0.0-fake")
os.environ.setdefault("MODELS__CHANGE_DETECTION__VERSION", "v0.0-fake")

# 伪模型缺失时自动生成（P-012 联调主线，不依赖真实模型交付）
if not (MODELS_DIR / "landcover-seg" / "v0.0-fake" / "int8.onnx").is_file():
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_fake_models.py"), "--out", str(MODELS_DIR)],
        check=True,
    )
