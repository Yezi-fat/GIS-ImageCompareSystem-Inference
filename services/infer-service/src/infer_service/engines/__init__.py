"""推理引擎包（设计 §2.3/§3.1，FR-5.1/5.2/3.2）。

统一抽象 InferenceEngine（Protocol）：语义分割与变化检测两类模型统一接口，
analysis 编排层只依赖该接口；本地 GPU/CPU 与远程实现完全同形，
切换 provider 不引起业务代码变更（需求 8.5）。
"""
