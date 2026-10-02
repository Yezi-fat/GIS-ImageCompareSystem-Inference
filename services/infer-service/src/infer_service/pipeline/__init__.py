"""影像处理管线包（设计 §2.3/§3.2）。

loader → tiler → engine → 融合 → postprocess → colorize；
编解码工具沉淀在 cv-common（imaging.py），本包只做管线逻辑。
M1 骨架里程碑：函数签名完整，实现留空（P-010/P-011/P-018a）。
"""
