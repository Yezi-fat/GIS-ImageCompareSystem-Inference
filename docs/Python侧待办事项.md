# Python 侧待办事项（联调后续）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 编写日期 | 2026-09-12 |
| 编写方 | Java 业务服务群 |
| 背景 | 《Java 侧待确认与待修改事项》V1.0 已全部逐条回复（见 `docs/Java侧待确认与待修改事项-回复.md`，A/B/C 全接受并已实现）；首轮全链路联调实测通过（伪模型 v0.0-fake）。本文收敛**剩余需 Python 侧执行/核对的事项** |
| 结论总览 | 无联调阻塞项残留；必做 2 项（真实模型交付形态、模型目录挂载方式），部署核对 3 项，契约保持 4 项 |

---

## 1. 必做（真实模型交付前）

### 1.1 真实模型按双产物 + 目录前缀形态交付（A-2 已确认约定）

- 每版本模型须同时产出 **fp32.onnx + int8.onnx 两件**（联调实测 infer-service 加载时两件齐备校验，缺 int8 即 `MODEL_NOT_READY`）；
- 对象存储布局：`{storage_key 前缀}/fp32.onnx` + `{storage_key 前缀}/int8.onnx`（如 `models/landcover-seg/v2.2/fp32.onnx`），然后走 Java 登记接口（`POST /api/v1/models`，storage_key 填目录前缀）；
- Java 下发目标固定为 `{models.shared-dir}/{name}/{version}/fp32.onnx|int8.onnx`，请保持 ModelStore 按此组织懒加载（现状已一致）。

### 1.2 模型目录挂载方式二选一（决定 Java 自动下发链路是否生效）

联调环境 infer-service 的 `/models` 为 Python 项目侧**只读 bind mount**，Java 的「登记 → 下发 → 激活热切换」自动链路无法写入。真实模型启用前请二选一：

- **方案甲（推荐）**：infer-service 改挂 mapchange compose 的 `models` 命名卷（与 config-service 共享）→ Java 登记接口自动下发，激活即热切换；
- **方案乙**：保持运维手工放置模型文件到 `{name}/{version}/`，Java 侧仅登记元数据（单文件形态登记会告警缺 int8，请用目录前缀形态登记并保证文件已就位）。

选定后告知 Java 侧同步部署文档即可，无需改代码。

## 2. 部署核对（上线前 checklist）

| # | 事项 | 口径 |
| --- | --- | --- |
| 2.1 | `AUTH_ENABLED` / `INTERNAL_TOKEN` 注入核对 | 两侧同一环境变量同值（B-5；Python 侧顶层扁平字段已知悉）；`/health` 恒豁免 |
| 2.2 | infer 多实例形态 | Java 为单地址直连（`PYTHON_INFER_HOST/PORT`），多实例请在 infer 前挂反向代理/负载均衡，对 Java 透明；Java 不做客户端轮询（理由见回复文档 B-5） |
| 2.3 | nlp 地理编码提供方 | 联调实测 `geocoder.available=false`（nominatim 未配置），当前 rule-based + 地名词典降级可用；生产若需更高地理编码/NLU 精度，请配置 LLM 与地理编码提供方（不配置不阻塞，FR-9.7 降级语义已由 `nlu_provider` 如实标记） |

## 3. 契约保持项（请勿单方面变更；确需变更先同步 Java 侧）

| # | 事项 | 原因 |
| --- | --- | --- |
| 3.1 | pydantic 可选字段语义保持「缺省即默认」 | Java 已按 `@JsonInclude(NON_NULL)` 不下发 null 字段（显式 null 会触发 422 `detail[]`，联调实测）；Python 侧若改为接受 null 也请保持向后兼容 |
| 3.2 | `IMAGE_DECODE_FAILED` 的 URL 过期场景 message 须稳定含 `403` 字样（如「存储端 HTTP 403」） | Java 编排层据此触发「重签 URL 重试一次」（A-3②）；字样变化会导致退化为 INVALID_INPUT 直失败 |
| 3.3 | 错误码表（接口文档 §4 十码）与错误包结构 `{code, message, trace_id}` 保持稳定 | Java `PythonErrorDecoder` 按 code 显式映射；新增错误码请提前告知，避免落入 HTTP 状态兜底 |
| 3.4 | 三服务 `/health` 结构与 OpenAPI 快照 | `gpu_usable` 是 Java 三级解析 GPU 判定依据、`models.segmentation.class_ids` 是 Java 前置校验（B-4）数据源；接口变更后执行 `scripts/export_openapi.py` 重导快照并同步 |

## 4. 知悉项（无需动作）

- Java 下发的 `element_catalog` 会多带 `color` 字段（前端目录复用同一 DTO），pydantic 忽略即可；
- Java 落库只存 storage key、签名 URL 出口实时签发；Python 侧无需感知存储写路径（蒙版 base64 随响应返回、Java 代收上传的 R-02 口径不变）；
- 远程推理平台（REMOTE provider，FR-5.4）为独立联调线，与本地 Python 链路互不影响；Java 侧 remote 请求/响应按《推理服务联调接口规范》显式映射，不复用本地契约；
- 联调期 Java 侧 L2 模型版本已指向 `v0.0-fake`（`inference.local-seg-model-version` / `local-change-model-version`）；真实模型登记激活后该值自动热切换，Python 无感知。
