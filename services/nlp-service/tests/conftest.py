"""nlp-service 测试公共配置：鉴权关闭；熔断器与配置在 fixtures 中按需重置。"""
import os

os.environ.setdefault("AUTH_ENABLED", "false")
