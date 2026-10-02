# Java 侧待确认与待修改事项

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 编写日期 | 2026-09-12 |
| 编写方 | Python 推理计算服务群 |
| 关联 | 《Python推理计算服务对Java侧服务接口需求文档》V1.1（J-01~J-10，本文为其状态收敛与补充）、Java业务服务接口文档 V1.1 |
| 背景 | Python 侧 M1~M5 已全部完成（设计 V2.6 / 清单 V2.6 / 需求 V2.8 口径），待进入联调 |

> 本文收敛**需要 Java 侧确认或修改的全部事项**。J-03（geo_extent 透传）与 J-06（element_catalog 下发）已在评审中确认契约（设计 V2.5 落实），不在此重复，但 Java 侧需**实现**相应透传逻辑（见 C-2）。

## A. 联调阻塞项（契约级，联调前必须确认）

### A-1. J-01：provider 枚举格式映射

- **Python 口径**：请求头 `X-Inference-Provider` 与响应 `actual_provider` 均为小写连字符 `remote / local-gpu / local-cpu`（设计 §1.3/§3.1.4）；
- **Java 实测口径**（Java 接口文档 §1.1）：`inference.provider: "local_cpu"`、`capabilities.inference_provider: "LOCAL_CPU"`（下划线/大写）；
- **需确认**：Java 调 Python 时按小写连字符发送（映射由 Java 做），Python 返回值 Java 自行映射回对外口径；或双方另约。

### A-2. J-05：模型双产物（FP32+INT8）与登记 storage_key 的对应约定

- Python 侧已按 `{models.dir}/{name}/{version}/{fp32,int8}.onnx` 落地（懒加载按名+版本）；
- Java `POST /api/v1/models` 登记体仅单一 `storage_key`（示例 `models/landcover-seg-v2.2.onnx`），与 FR-3.2"每模型 FP32+INT8 两份"存在对应关系缺口；
- **需确认**：① storage_key 指向目录/前缀还是清单文件；② Java 下发到共享卷时目标命名是否固定为 `{name}/{version}/fp32.onnx|int8.onnx`。
- 详细流程见 `docs/模型交付流水线.md` 第 4 节。

### A-3. J-07：错误码映射表回传

- Python 内部码 9 个 + 兜底（见《Python推理计算服务接口文档》§4）；其中三个已在评审中定死**恒等映射**：`GEO_EXTENT_MISMATCH`(400)、`ALIGNMENT_FAILED`(422)、`LOCATION_UNRESOLVED`(422)；
- **需 Java**：① `PythonErrorDecoder` 全覆盖回传实际映射表；② 明确 `IMAGE_DECODE_FAILED` 的分流口径——存储端 403（URL 过期）触发**重签重试一次**（J-02），其余归 INVALID_INPUT；③ **Java 接口文档 §0 错误码列表补录 LOCATION_UNRESOLVED(422)**（需求 §7.7 V2.7 已补，Java 文档 V1.1 缺）。

## B. 联调配合项（行为级，联调期间验证）

| # | 事项 | 要求 |
| --- | --- | --- |
| B-1 | **J-02 预签名 URL 签发时机** | 须在任务执行期（Worker 消费后）签发，或 TTL 覆盖"排队+执行"上限（默认 10min 在队列积压时可能不够）；local 存储模式下 URL 须为 Python 容器可达的内网地址（容器网络拓扑，WSL2 宿主机供图不通的坑见 checkpoint-2026-09-12-1245 排坑 4） |
| B-2 | **J-09 Java→infer 超时分档** | 不能套用远程推理 30s：同步小图短超时；大图/多期走异步队列后 TaskWorker 调 infer 的超时按需求 8.1 上限（4096² CPU ≤15min）配置；Python 信号量排队余量另计 |
| B-3 | **J-08 /health 聚合口径** | Java 三级解析依赖 infer /health 的 `gpu_available/gpu_usable/vram_mb/models.*.class_ids/remote_configured`；nlp /health 的 `nlu.circuit`；聚合失败降级 provider="unknown" + partial=true（Java 接口文档 §3.2 已实现，联调验证） |
| B-4 | **J-04 class_mapping 一致性校验归属** | 建议 Java 在任务编排期校验（element_catalog.model_class_id 与 infer /health 的 class_ids 对照），超范围直接返 UNSUPPORTED_ELEMENTS，不再调用 Python（Python 侧 FR-6.6 兜底已在） |
| B-5 | **J-10 部署协同** | `AUTH_ENABLED`/`INTERNAL_TOKEN` 同一环境变量注入两侧（注意：Python 侧为顶层扁平字段——pydantic-settings 嵌套别名不可靠，设计 V2.6 注记⑤）；infer 多实例时 Java 直连地址列表与轮询策略确认 |

## C. Java 侧待实现/待修改（文档或代码）

| # | 事项 | 说明 |
| --- | --- | --- |
| C-1 | **Java 接口文档 §0 补 LOCATION_UNRESOLVED(422)** | 需求 V2.7 已补录，Java 文档 V1.1 未同步 |
| C-2 | **实现 geo_extent / element_catalog 透传** | J-03/J-06 契约已确认：`/infer/*` 请求体携带 `geo_extent`（必填）、`/nlp/parse` 携带 `element_catalog`（必填，自 config-service 取出）——Java 侧调 Python 的组装逻辑需按此实现/核对 |
| C-3 | **change-detection 链路透传 auto_align + 新响应字段** | Python 响应含 `estimated_shift_px`/`auto_aligned`（评审 D-03）；Java 编排层需透传/落库；`auto_align=true` 超偏差时 Python 返 ALIGNMENT_FAILED（422）——Java 任务失败语义映射 |
| C-4 | **需求文档 FR-7.7 行文本口径注**（需求侧文档，同步 Java 认知） | 本期双期比对链路无像素级配准校验（分请求到达），由 Java geo_extent 一致性校验兜底范围级偏差；像素级随 M6 落地——建议需求文档 FR-7.7 补注，避免验收争议 |

## D. 联调启动建议顺序

1. A-1/A-2/A-3 三项契约确认（半天会议量）；
2. B-1/B-2 配置就位后跑通最小链路：`feature-extraction`（小图同步）→ `feature-comparison`（异步）→ `temporal-analysis`；
3. `nlp/parse` 联调（依赖 C-2 的 element_catalog 传递）；
4. B-3/B-4/B-5 与 C-1/C-3/C-4 随联调并行收敛。
