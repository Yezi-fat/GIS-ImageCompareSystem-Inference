"""请求/响应 Pydantic 模型——三服务契约的唯一事实源（设计 §4）。

任何 schema 变更只允许修改本包（全局要求 #2），
变更后须重新导出 docs/openapi/ 三份快照并与 Java 侧 diff。
"""
