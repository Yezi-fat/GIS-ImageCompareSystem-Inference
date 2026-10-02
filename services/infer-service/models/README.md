# 模型文件挂载点（容器 volume）

模型按 `{name}/{version}/{fp32|int8}.onnx` 组织（评审 P-02/J-05）：

```
models/
├── landcover-seg/
│   ├── v2.0/
│   │   ├── fp32.onnx      # GPU 用 FP32（FR-3.2）
│   │   └── int8.onnx      # CPU 用 INT8 量化版
│   └── v2.1/ ...
└── change-detection/
    └── v1.2/
        ├── fp32.onnx
        └── int8.onnx
```

引擎按请求携带的 `model_name`/`model_version` 懒加载并缓存会话；
Java 激活切换经运行配置热生效随请求下发，无需重启（FR-3.5）。
模型文件下发方式（对象存储 + 命名卷）与登记 `storage_key` 的对应约定
待 Java 侧确认（《Python推理计算服务对Java侧服务接口需求文档》J-05）。
