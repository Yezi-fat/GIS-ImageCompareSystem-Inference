# Java 侧待确认与待修改事项——逐条回复

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 编写日期 | 2026-09-12 |
| 编写方 | Java 业务服务群 |
| 针对 | 《Java 侧待确认与待修改事项》V1.0（Python 侧，2026-09-12） |
| 结论总览 | **A-1/A-2/A-3、B-1~B-5、C-1~C-4 全部接受**；A-2、B-1、B-5 附约定/说明；代码侧已同步完成契约对齐（见文末「Java 侧已完成改动清单」） |

---

## A. 联调阻塞项

### A-1. J-01：provider 枚举格式映射 —— **接受**

- **口径**：`X-Inference-Provider` 请求头与 `actual_provider` 响应字段均为小写连字符（`remote`/`local-gpu`/`local-cpu`），**映射由 Java 做**，Python 无需感知 Java 对外口径。
- **已实现**：
  - Java → Python：调 `/infer/*` 时按三级解析结果发送提示头（`InferenceRouter.toPythonProviderHint`）；远程熔断降级回放本地时**不带提示头**，由 infer-service 本地默认配置兜底（符合 §0.2「缺省兜底」语义）；
  - Python → Java：`actual_provider` 映射回对外口径（`local-cpu` → 分析结果 `inference.provider="local_cpu"`、任务详情 `provider="LOCAL_CPU"`），结果以 `actual_provider` 为准，缺省时回退 Java 侧解析结果；
  - 同步/异步链路分别走 `python-infer-sync` / `python-infer` 两个 Feign 通道（见 B-2），提示头两通道一致。

### A-2. J-05：模型双产物（FP32+INT8）与 storage_key 约定 —— **接受（约定如下，已经联调实测校准）**

- **① storage_key 形态约定**（已实现于 `ModelRegistryService`）：
  - **目录前缀（推荐，FR-3.2 双产物）**：如 `models/landcover-seg/v2.2/`，其下固定两个对象 `{prefix}fp32.onnx` 与 `{prefix}int8.onnx`，**均为必需**——2026-09-12 联调实测 infer-service 加载时两件齐备校验、缺 `int8.onnx` 即返 `MODEL_NOT_READY`，故 Java 登记时缺任一件直接拒绝（INVALID_INPUT）；
  - **单文件 `.onnx`（兼容旧登记）**：视作 fp32 单产物下发并记 WARN（Python 侧将因缺 int8 不可加载，仅供元数据登记/历史版本场景，不建议用于待激活版本）；
- **② 下发命名固定**：`{models.shared-dir}/{name}/{version}/fp32.onnx|int8.onnx`，与 Python ModelStore `{name}/{version}/` 组织一致；激活切换链路（写 L2 键对热生效）不变。
- **宿主机部署注记**：联调环境中 infer-service 的模型目录为其项目侧 bind mount（只读），`v0.0-fake` 伪模型已就位；Java 侧 L2 `inference.local-seg-model-version`/`local-change-model-version` 需指向已就位的版本（联调期 = `v0.0-fake`）。容器化统一部署后再恢复 config-service 写共享卷、infer-service 读的自动下发链路。

### A-3. J-07：错误码映射表回传 —— **接受**

- **① `PythonErrorDecoder` 已全覆盖**（10 码 + 兜底），实际映射表回传如下：

| Python 内部码（HTTP） | Java 对外码（HTTP） | 说明 |
| --- | --- | --- |
| UNSUPPORTED_ELEMENT(400) | UNSUPPORTED_ELEMENTS(400) | 复数形态为 Java 对外既有码 |
| IMAGE_DECODE_FAILED(400) | INVALID_INPUT(400) | message 含存储端 403 时走重签重试（见②） |
| GEO_EXTENT_MISMATCH(400) | GEO_EXTENT_MISMATCH(400) | 恒等（评审定死） |
| INPUT_TOO_LARGE(400) | INPUT_TOO_LARGE(400) | 恒等 |
| ALIGNMENT_FAILED(422) | ALIGNMENT_FAILED(422) | 恒等（评审定死） |
| LOCATION_UNRESOLVED(422) | LOCATION_UNRESOLVED(422) | 恒等（评审定死）；ErrorCode 已新增 |
| INFERENCE_FAILED(500) | INFERENCE_UNAVAILABLE(502) | 对外统一推理不可用语义 |
| MODEL_NOT_READY(503) | INFERENCE_UNAVAILABLE(502) | 同上（模型缺失属推理不可用） |
| NLU_UNAVAILABLE(503) | NLU_UNAVAILABLE(503) | 恒等 |
| INTERNAL_ERROR(500) | INTERNAL_ERROR(500) | 恒等 |
| （无 code/未知 code） | 按 HTTP 状态兜底 | 400→INVALID_INPUT，422→ALIGNMENT_FAILED，429→RATE_LIMITED，502→INFERENCE_UNAVAILABLE，503→NLU_UNAVAILABLE，504→INFERENCE_TIMEOUT，其余→INTERNAL_ERROR |

  另保留 `EXTENT_MISMATCH`→`GEO_EXTENT_MISMATCH` 兼容项（远程平台联调规范 §6 旧称）。
- **② IMAGE_DECODE_FAILED 分流口径**：Python `message` 携带存储端 HTTP 状态——**含 "403"（URL 过期/失效）→ Java 编排层重签 URL 并重试一次**（仍失败按 INVALID_INPUT 登记任务失败）；其余（400/404/解码失败等）→ INVALID_INPUT。请 Python 侧保证 403 场景 message 中稳定出现 `403` 字样（如「存储端 HTTP 403」）。
- **③ Java 接口文档 §0 已补录 `LOCATION_UNRESOLVED(422)`**（随本次 C-1 完成，文档 V1.3）。

## B. 联调配合项

### B-1. J-02 预签名 URL 签发时机 —— **接受（现状已满足，附说明）**

- **现状**：内网预签名 URL 在 **Worker 消费后、执行期签发**（`signInternal` 位于编排执行体内，非请求接收期），TTL（`storage.sign-url-internal-ttl`，默认 PT10M，L2 可调）只需覆盖**执行窗口**，队列积压不消耗 TTL——满足 J-02 首选口径；
- **兜底**：执行期内 URL 仍过期（存储端 403）→ 重签重试一次（A-3②）；
- **local 模式内网可达性**：`storage.local.internal-base-url` 已参数化（`STORAGE_INTERNAL_BASE_URL`）；compose 默认 `http://storage-service:8084`（联调环境 Python 容器已接入 `mapchange_default` 网络，实测可达）；Python 纯宿主机部署时改 `http://localhost:8084`（compose 已映射 storage-service 8084 端口）——WSL2 宿主机供图不通的坑即由该参数规避。
- **注意**：Python 侧部署形态变化（容器化↔宿主机）只需调整该参数与 `PYTHON_*_HOST/PORT`，无需改代码。

### B-2. J-09 Java→infer 超时分档 —— **接受（已实现）**

- infer 拆双通道（同地址同契约，独立超时）：
  - `python-infer-sync`（Web 线程同步小图）：connect 5s / read **60s**（同步小图快速失败）；
  - `python-infer`（TaskWorker 异步）：connect 5s / read **960s**（= 需求 8.1 上限 15min + 1min 余量；Python 信号量排队余量由该余量与编排层重试共同覆盖）；
  - compute：read 300s；nlp：read 70s（覆盖 LLM 超时+规则降级）；
- 全部可用环境变量调整（`PYTHON_INFER_SYNC_READ_TIMEOUT_MS` / `PYTHON_INFER_ASYNC_READ_TIMEOUT_MS` / `PYTHON_COMPUTE_READ_TIMEOUT_MS` / `PYTHON_NLP_READ_TIMEOUT_MS` / 各 `*_CONNECT_TIMEOUT_MS`）；
- 远程平台（REMOTE provider）超时语义不变：`inference.remote.timeout-ms`（L2）+ resilience4j 重试/熔断。

### B-3. J-08 /health 聚合口径 —— **接受（已对齐）**

- Java 健康 DTO 已对齐真实结构：`gpu_available`/`gpu_usable`/`vram_mb`/`models.{segmentation,change_detect}.{loaded,version,provider,class_ids}`/`remote_configured`；
- 三级解析 GPU 判定改用 **`gpu_usable`**（试建会话通过）——比 gpu_available 更严格，避免「有卡不可用」误判；
- `nlp /health` 的 `nlu.circuit`：provider 解析时顺带探测，非 `closed` 记 WARN 日志（监控告警口径）；nlp 不可达不影响主链路；
- 聚合失败 → `provider="unknown"` + `partial=true` 为既有实现（capabilities 30s 缓存、单边 2s 超时），联调验证即可。

### B-4. J-04 class_mapping 一致性校验归属 —— **接受（已实现）**

- `AnalysisRequestValidator` 在目录校验（FR-6.6）之后，对照**最近一次 infer /health 上报的 `models.segmentation.class_ids`** 校验各要素 `model_class_id`，超范围直接返 `UNSUPPORTED_ELEMENTS`，不再调用 Python；
- health 不可得（未探测/探测失败/强制 provider 未走 auto 探测）时跳过该校验，Python 侧 FR-6.6 兜底仍生效。

### B-5. J-10 部署协同 —— **接受（infer 多实例轮询另约）**

- `AUTH_ENABLED`/`INTERNAL_TOKEN` 同一环境变量注入两侧：compose 侧 `java-env` 锚点统一注入（已知悉 Python 侧为顶层扁平字段，pydantic-settings 嵌套别名不可靠）；
- **infer 多实例**：Java 侧保持**单地址直连**（`PYTHON_INFER_HOST`/`PYTHON_INFER_PORT`）。多实例部署请在 infer 前挂反向代理/负载均衡（对 Java 透明）——理由：Java 侧已有同步/异步双通道、熔断降级与编排层重试语义，客户端轮询会与重试策略叠加放大尾部时延；如确需 Java 客户端多地址列表+轮询，可作为后续增强单独立项，不阻塞本期联调。

## C. Java 侧待实现/待修改

| # | 结论 | 落实情况 |
| --- | --- | --- |
| C-1 接口文档补 LOCATION_UNRESOLVED(422) | **接受** | 已补（接口文档 V1.3 §0 错误码表） |
| C-2 geo_extent / element_catalog 透传 | **接受（已实现）** | `/infer/*` 请求体携带 `geo_extent`（float[4] 数组，必填）；`/nlp/parse` 携带 `element_catalog`（自 config-service 实时取出，`[{id,name,color,model_class_id,enabled}]`，`color` 为多出字段可忽略）。另修复编排层缺陷：异步链路此前用 payload 中的占位目录项（model_class_id=0）组包，现经目录解析后下发真实 `class_mapping`/`colors` |
| C-3 change-detection 透传 auto_align + 新响应字段 | **接受（已实现）** | `auto_align` 透传；响应 `estimated_shift_px`/`auto_aligned` 落入任务结果 JSON（接口文档 §1.4 已补）；`ALIGNMENT_FAILED(422)` → 任务 FAILED 且 `error_code=ALIGNMENT_FAILED`（同步接口则 422 直返前端） |
| C-4 需求文档 FR-7.7 口径注 | **接受** | 已在需求文档 FR-7.7 行补注：本期双期比对链路无像素级配准校验（两期分请求到达 infer），范围级偏差由 Java geo_extent 一致性校验（FR-8.7）兜底，像素级配准校验随 M6 落地 |

## D. 联调启动建议顺序 —— 认同，已按此顺序完成首轮实测

Java 侧已就绪：A-1/A-2/A-3 本回复确认 + 代码落地；B-1/B-2 配置已就位。**2026-09-12 首轮全链路实测结果**（伪模型 v0.0-fake）：

| 链路 | 结果 |
| --- | --- |
| capabilities 聚合（真实 infer /health） | ✅ `inference_provider=LOCAL_CPU`，`partial=false` |
| `feature-extraction` 小图同步 | ✅ 真实 area_m2 椭球统计 + 合成/分类蒙版 + eager 矢量化 |
| `feature-comparison` 异步（segment×2 + diff base64 + geo_transform 透传） | ✅ 双单位差异统计（added_m2/removed_m2 生效） |
| `temporal-analysis` 3 期 | ✅ 面积序列/相邻期变化率/N-1 差异组 |
| `change-detection`（auto_align 透传） | ✅ `estimated_shift_px`/`auto_aligned` 落结果 |
| `nl-task/parse`（element_catalog 下发） | ✅ rule-based 降级链路，FR-9.4/9.8 字段齐全 |
| GeoJSON 导出 + 懒矢量化回填（result-service → compute base64） | ✅ Feature 含 geometry/area_m2/centroid/bbox |
| 错误映射实测：MODEL_NOT_READY → INFERENCE_UNAVAILABLE；IMAGE_DECODE_FAILED(401) → INVALID_INPUT | ✅ |
| 契约快照 | ✅ openapi-diff 全绿（analysis-service 快照随 `unsupported` 对象化更新） |

**联调期发现的契约注意点（请 Python 侧保持）**：
1. pydantic 可选字段不接受显式 `null`（如 `min_area: null` → 422 `detail[]`）——Java 已改为 null 字段不下发（`@JsonInclude(NON_NULL)`），Python 侧勿改为「必需但可空」之外的第三形态；
2. FastAPI 请求校验 422 返回 `{"detail":[...]}`（非业务错误包）——Java 已识别并映射 INVALID_INPUT；
3. 403 重签重试依赖 message 中的 `403` 字样（A-3②），请保持「存储端 HTTP 403」表述稳定。

---

## 附：Java 侧已完成改动清单（2026-09-12，供 Python 侧知悉对齐口径）

1. **DTO 全面对齐真实契约**（此前 Java 按旧桩契约实现，字段名/结构均不符）：
   - `/infer/segmentation`：请求 `image_url`/`geo_extent[4]`/`elements`/`class_mapping`/`colors`/`min_area`/`model_name`/`model_version`；响应取 `combined_mask_png_b64`/`per_class_masks`/`statistics`/`geo_transform`/`model_info`/`actual_provider`/`elapsed_ms`；
   - `/infer/change-detection`：响应取 `mask_png_b64`/`probmap_png_b64`/`statistics`/`estimated_shift_px`/`auto_aligned`/`geo_transform`/`model_info`/`actual_provider`；
   - `/compute/diff`：改为 **`before_mask_b64`/`after_mask_b64` base64 上行**（不再传预签名 URL）+ `colors{added,removed}` 对象 + `geo_transform`；蒙版 base64 由分割响应留存直传，无存储往返；
   - `/compute/vectorize`：改为 `mask_b64` + `min_area` + `geo_transform`；响应 `regions[{geojson,area_px,area_m2,centroid,bbox}]` 由 Java 组装为 regions.geojson Feature（properties 带 period/element/region_type/area/centroid/bbox）；懒矢量化（result-service 导出时现算）同口径，蒙版从存储取回编码；
   - `/nlp/parse`：`unsupported` 按 `[{raw, reason}]` 对象数组对齐；
   - 三服务 `/health` DTO 全部对齐真实结构。
2. **geo_transform 兜底推导**：infer 响应缺 `geo_transform` 时（remote provider 路径），Java 按「geo_extent + 蒙版 PNG 像素尺寸」线性推导（与 Python PNG 口径一致，只读 IHDR 头，不做像素计算）。
3. **provider 提示头 + actual_provider 回映射**（A-1）；**超时分档**（B-2）；**错误码全覆盖 + 403 重签重试**（A-3）；**class_mapping 校验前置**（B-4）；**模型双产物下发**（A-2）。
4. **部署**：WireMock 打桩移入 `stub` profile（默认不启动，打桩响应已同步更新为新契约）；compose 默认经 `host.docker.internal` 访问宿主机 Python 三服务；storage-service 映射 8084 供 Python 拉图；全部地址/超时均为环境变量参数。
