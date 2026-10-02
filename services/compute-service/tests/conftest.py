"""compute-service 测试公共配置：鉴权关闭（auth 两态由 cv-common 测试覆盖）。"""
import os

os.environ.setdefault("AUTH_ENABLED", "false")
