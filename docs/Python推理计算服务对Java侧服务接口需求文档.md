# Python 推理计算服务对 Java 侧服务接口需求文档

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.1（J-03/J-06/J-07 随评审反馈更新，2026-09-11） |
| 编写日期 | 2026-09-11 |
| 编写方 | Python 推理计算服务群（infer-service :8001 / compute-service :8002 / nlp-service :8003 + cv-common） |
| 依据 | 需求分析文档 V2.6、Python 详细设计文档 V2.4、Java 业务服务接口文档 V1.1 |
| 关联文档 | 《Python侧设计评审与任务清单问题（待确认）》（D-01/D-02/D-04 结论影响本文 J-03/J-06/J-07） |

## 0. 背景与调用关系

Python 三服务为无状态内网计算黑盒，仅被 Java 侧调用：`analysis-service → infer/compute/nlp`，`result-service → compute`（懒矢量化），配置地址直连、不入 Nacos。本文整理 Python 侧**需要 Java 侧提供或共同确认的接口配合事项**，共 10 项（J-01~J-10）。标注【契约】的项影响 cv-common schema 与 OpenAPI 快照，需在 M1 冻结前确认；其余为联调/部署配合项。

## 1. 请求头与通用约定

### J-01 【契约】内部调用请求头约定

Java 调用 Python 任意接口时携带：

| 请求头 | 必选 | 说明 |
| --- | --- | --- |
| `X-Internal-Token` | auth 开启时必选 | 与 Java 共享密钥（`INTERNAL_TOKEN` 环境变量注入）；缺失/错误 → 401 UNAUTHORIZED；`/health` 恒豁免 |
| `X-Task-Id` | 任务链路必选 | 透传任务 ID，写入 Python 结构化日志（全链路追踪） |
| `X-Trace-Id` | 建议 | 链路追踪 ID |
| `X-Inference-Provider` | 仅 infer 接口 | 取值 `remote` / `local-gpu` / `local-cpu`（**小写连字符**）；缺省时 Python 按本地默认配置兜底 |

**需确认**：provider 枚举格式统一为小写连字符（Python 响应 `actual_provider` 同口径）；Java 侧对外展示口径 `local_cpu`/`LOCAL_CPU`（接口文档实测值）由 Java 自行映射转换。

## 2. 影像与数据传递

### J-02 【联调】影像预签名 URL 的签发时机与 TTL

- Python 按 Java 签发的内网预签名 URL 直取影像（零配置零令牌，评审 P-03）；
- **要求**：URL 须在**任务执行期（Worker 消费后）签发**，而非提交期；或 TTL 覆盖"排队等待 + 推理执行"上限——默认 10min TTL 在队列积压时可能过期；
- URL 过期/失效时 Python 返回 `IMAGE_DECODE_FAILED`（将在错误 message 中携带存储端 HTTP 状态，如 403），**请 Java 识别后重签 URL 并重试一次**（对应评审 R-03 URL 重签）；
- local 存储模式下，Python 容器须能经内网访问 storage-service 文件下载地址。

### J-03 【契约，✅已确认 2026-09-11】infer 请求需新增 geo_extent 字段

PNG/JPEG 瓦片拼接图无内嵌 CRS，而响应统计含 `area_m2`（Java 实测已对 PNG 产出该值）。**已确认采方案①（评审 D-01，设计 V2.5 已落实）**：`POST /infer/segmentation` 与 `POST /infer/change-detection` 请求体新增：

```json
"geo_extent": [104.01, 30.69, 104.07, 30.73]
```

**必填**，CGCS2000 经纬度——PNG 场景由 Java 透传前端入参；GeoTIFF 场景亦下发，用于与内嵌 transform 交叉校验（偏差超容差 → GEO_EXTENT_MISMATCH）。Python 据此 + 图像尺寸经 cv-common `extent_to_geo_transform` 推导 geo_transform 并随响应返回，Java 再透传给 `/compute/diff`、`/compute/vectorize` 的 `geo_transform` 字段（compute 侧契约不变）。

### J-04 【契约】业务策略参数逐请求下发（确认已有约定，无需变更）

以下参数由 Java 运行配置/要素目录持有，逐请求随 body 下发，Python 不落库不缓存：

- `/infer/segmentation`：`elements`、`class_mapping`（要素→模型类别 ID，来自 element_catalog.model_class_id）、`colors`（要素配色）、`min_area`；
- `/infer/change-detection`：`threshold`、`min_area`；
- `/compute/diff`：`colors`（diff 分色 added/removed，来自 capabilities defaults.diff_colors）、`min_area`、`geo_transform`；
- `/compute/vectorize`：`min_area`、`geo_transform`。

**请 Java 侧确认**：class_mapping 取值与 infer-service `/health` 上报的 `models.segmentation.class_ids` 一致性校验由谁执行（建议 Java 在任务编排期校验，超范围直接返 UNSUPPORTED_ELEMENTS，不再调用 Python）。

## 3. 模型管理配合

### J-05 【需确认】模型双产物（FP32+INT8）与登记 storage_key 的对应约定

- Python 侧模型按 `{models.dir}/{name}/{version}/{fp32|int8}.onnx` 组织，按 `model_name`+`model_version` 懒加载（FR-3.2/3.5）；
- Java `POST /api/v1/models` 登记体仅含**单一 `storage_key`**（示例：`models/landcover-seg-v2.2.onnx`），与 FR-3.2"每模型交付 FP32+INT8 两份产物"存在对应关系缺口；
- **需确认**：① storage_key 指向**目录/前缀**（其下含两份 onnx），还是指向**清单文件**（manifest 描述两产物位置）；② Java 下发到共享卷时的目标命名是否固定为 `{name}/{version}/fp32.onnx` 与 `int8.onnx`（Python 按此约定取文件）；
- 激活切换：Java 改运行配置热生效后**逐请求携带 model_name/model_version** 即可，Python 无需通知、无需重启（设计 §3.1.2，评审 P-02）。

## 4. NLP 服务配合

### J-06 【契约，✅已确认 2026-09-11】/nlp/parse 请求须携带 element_catalog

Python nlp-service 无状态、不读库，要素映射依赖请求下发。**已确认（评审 D-02，设计 V2.5 已落实）**：

```json
{"text": "...", "element_catalog": [{"id": "forest", "name": "森林", "model_class_id": 1, "enabled": true}, ...]}
```

① `element_catalog` **必填**，Java 从 config-service 要素目录取出随请求下发（含 id/name/model_class_id/enabled，为 NLU 解析与要素映射的事实源）；② 要素**别名/修饰语词表**（"建筑群"→building、"雪山"→snow）**由 nlp-service 内置维护**，不经 Java 下发、不入配置——Java 侧无需任何配合。

### J-07 【契约，部分确认 2026-09-11】错误码映射表确认

Python 内部错误码（设计 V2.5 §4.6，评审 D-03/D-04/D-05 已落定）需 Java `PythonErrorDecoder` 全覆盖：

| Python 内部码 | HTTP | 建议对外映射 | 状态 |
| --- | --- | --- | --- |
| UNSUPPORTED_ELEMENT | 400 | UNSUPPORTED_ELEMENTS（400） | 待 Java 确认 |
| IMAGE_DECODE_FAILED | 400 | INVALID_INPUT / 或触发 URL 重签重试（J-02） | 待 Java 确认 |
| GEO_EXTENT_MISMATCH（原 EXTENT_MISMATCH 已更名，D-05） | 400 | GEO_EXTENT_MISMATCH（400）——**恒等映射**，双期尺寸不一致与多期范围不一致共用一码、差异由 message 承载 | ✅ 码值/状态已定 |
| INPUT_TOO_LARGE | 400 | INPUT_TOO_LARGE（400） | 待 Java 确认 |
| INFERENCE_FAILED | 500 | INTERNAL_ERROR（500）/ INFERENCE_UNAVAILABLE（502） | 待 Java 确认 |
| MODEL_NOT_READY | 503 | INFERENCE_UNAVAILABLE（502）/ INTERNAL_ERROR | 待 Java 确认 |
| NLU_UNAVAILABLE | 503 | NLU_UNAVAILABLE（503） | 待 Java 确认 |
| ALIGNMENT_FAILED | 422 | ALIGNMENT_FAILED（422）——恒等映射（D-03；message 注明自动配准本期未启用） | ✅ 码值/状态已定 |
| LOCATION_UNRESOLVED | 422 | LOCATION_UNRESOLVED（422）——恒等映射（D-04；需求 7.7 已补录，**Java 接口文档 §0 错误码列表待同步**） | ✅ 码值/状态已定 |

**请 Java 侧回传实际映射表**（含 IMAGE_DECODE_FAILED 触发重签的判断口径），双方冻结后写入各自契约测试。

## 5. 健康检查与超时

### J-08 【联调】/health 聚合字段与超时口径

- Java 三级解析（FR-5.6）与 capabilities 聚合依赖 infer-service `/health` 字段：`gpu_available / gpu_usable / vram_mb / cpu_cores / models.{segmentation,change_detect}.{loaded,version,provider,class_ids} / remote_configured`；nlp `/health` 的 `nlu.circuit`（closed/half_open/open）供熔断监控告警；
- **请确认**：Java 聚合调用的超时与降级口径（某下游超时 → capabilities 对应 provider="unknown"、partial=true，接口文档 §3.2 已实现）与轮询频率。

### J-09 【需确认】Java→infer-service 调用超时按任务规模分档

- 设计 §3.1.3 的 `timeout=25s` 仅约束 **Python→通用推理服务** 单次调用；
- **Java→infer-service** 的整体调用时长与输入规模强相关：1024² CPU 同步 ≤60s、4096² CPU 异步 ≤15min（需求 8.1）；**Feign/Resilience4j 的 read timeout 不能套用 30s**，建议按任务类型/规模分档配置（如同步 90s、异步大图 20min），或在异步链路采用"提交后立即返回、Python 完成后 Java 轮询/回调"之外的方案时特别注意；
- Python 侧推理信号量（CPU 2 槽/GPU 4 槽）会导致排队，Java 超时预算建议含排队余量。

### J-10 【部署】多实例直连与环境变量协同

- infer-service 多实例水平扩展时，Java 直连地址支持**配置多个地址轮询**（设计 §5）——请确认 Java 侧地址配置项形态（单地址 or 列表）与负载均衡策略；
- 部署协同：`AUTH_ENABLED` 与 `INTERNAL_TOKEN` 必须由**同一环境变量**注入 Java 与 Python 两侧（FR-10.7 语义一致性），compose/K8s 模板中请保持同源。

---

*确认进展（2026-09-11）：J-03/J-06 已随评审 D-01/D-02 确认并落实至设计 V2.5；J-07 中 GEO_EXTENT_MISMATCH/ALIGNMENT_FAILED/LOCATION_UNRESOLVED 三码已定（设计 V2.5 §4.6），其余映射待 Java 回传。**仍待确认**：J-01（provider 枚举映射）、J-05（模型双产物 storage_key 约定，M3 P-014 前需结论）、J-08/J-10 联调与部署项。schema 冻结依据已满足，可进入 M1 开发。*
