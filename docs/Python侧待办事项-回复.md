# Python 侧待办事项——逐条回复（附 Java 回复文档复核结论）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 编写日期 | 2026-09-12 |
| 编写方 | Python 推理计算服务群 |
| 针对 | 《Python 侧待办事项》V1.0（Java 侧，2026-09-12）、《Java 侧待确认与待修改事项-回复》V1.0、Java 业务服务接口文档 V1.4 |
| 结论总览 | **待办事项全部接受（无拒绝项）**；Java 回复文档复核无遗留问题（2 点澄清）；接口文档 V1.4 的 `unsupported` 对象数组变动与 Python 真实契约一致、Python 无需改动；Python 侧无需配置 Java 服务地址（理由见第 4 节） |

---

## 1. 对《Java 侧待确认与待修改事项-回复》的复核结论

**无其他问题**，A/B/C 各项接受与实现均与 Python 侧实际行为一致。两点澄清（非问题，避免口径偏差）：

1. **A-2「两件齐备校验」的精确语义**：infer-service ModelStore 按 provider 取文件——CPU 路径加载 `int8.onnx`、GPU 路径加载 `fp32.onnx`，缺对应文件即 `MODEL_NOT_READY`（联调实测的"缺 int8 即报缺"即 CPU 路径表现）。Java 登记侧"缺任一件直接拒绝"比 Python 略严格，**可接受且推荐保持**（提前暴露双产物缺失）。
2. **B-3 GPU 判定**：`gpu_usable`（试建会话通过）比 `gpu_available`（provider 列表存在）更严格——Java 三级解析采用 gpu_usable 与 Python 探测语义完全一致，无歧义。

Java 侧「联调期契约注意点」三条，Python 侧确认保持并均已锁定：

| 注意点 | Python 侧现状 | 锁定方式 |
| --- | --- | --- |
| 可选字段不接受显式 null（422 `detail[]`） | pydantic v2 非 Optional 字段即此语义，不引入第三形态 | 新增回归测试（本回复第 5 节 ③） |
| 422 校验错误返回 `{"detail":[...]}` | FastAPI 默认形态，Java 已映射 INVALID_INPUT，Python 保持不变 | 同上 |
| 403 重签依赖 message 含「403」 | 现行表述「影像拉取失败：存储端返回 HTTP 403（…可重签重试）」稳定 | 既有测试 `test_url_expired_403` 已断言 message 含 403 |

## 2. 《Python 侧待办事项》逐条结论

### 2.1（必做 1.1）真实模型双产物 + 目录前缀交付 —— **接受**

与 Python 现状完全一致：`{name}/{version}/{fp32,int8}.onnx` 组织、按请求名+版本懒加载（评审 P-02 落地）。执行口径以 `docs/模型交付流水线.md` 为准；对象存储目录前缀形态（`{prefix}fp32.onnx` + `{prefix}int8.onnx`）与 Java 登记/下发链路兼容，Python 无需改动。

### 2.2（必做 1.2）模型目录挂载方式 —— **接受，选择如下**

- **联调期：方案乙**（运维手工放置，现状即此——`/models` 为项目侧只读 bind mount，`v0.0-fake` 已就位，首轮联调已验证）；
- **生产/统一部署：方案甲**（infer-service 挂共享命名卷，Java「登记→下发→激活热切换」自动链路生效）。
- 切换仅需改 infer-service 的 volume 挂载，Python 代码零改动；compose 中已留注释说明。请 Java 侧部署文档按此口径记录。

### 2.3（部署核对 2.1）AUTH_ENABLED / INTERNAL_TOKEN 同源注入 —— **接受（已落实）**

Python 三服务 compose 已从 Java 侧同一环境变量注入（顶层扁平字段形态 Java 已知悉）；`/health` 恒豁免。上线前以「Java 调 /infer/segmentation 无令牌返 401、带令牌返 200」做对拍验证即可。

### 2.4（部署核对 2.2）infer 多实例形态 —— **接受 Java 单地址直连口径**

Python 三服务无状态，多实例部署时在 infer 前挂反向代理/负载均衡（对 Java 透明），Java 不做客户端轮询的理由（与重试/熔断策略叠加放大尾部时延）成立。Python 侧无需改动；如需多实例，属部署拓扑事项。

### 2.5（部署核对 2.3）nlp 地理编码/LLM 提供方 —— **接受（配置项已就绪）**

| 部署项 | 环境变量 | 说明 |
| --- | --- | --- |
| LLM 完整端点 | `NLP__LLM_URL` | 空 = 常驻规则解析（当前联调形态） |
| LLM 模型名 / 超时 | `NLP__LLM_MODEL` / `NLP__LLM_TIMEOUT_S` | 默认 qwen2.5-14b-instruct / 15s |
| 熔断参数 | `NLP__CIRCUIT__FAILURE_THRESHOLD` / `WINDOW_S` / `OPEN_DURATION_S` | 默认 5 次/60s/30s |
| 地理编码提供方 | `NLP__GEOCODER_PROVIDER` | amap / baidu / nominatim |
| 地理编码完整端点 / key | `NLP__GEOCODER_URL` / `NLP__GEOCODER_KEY` | 空 = 未配置（词典离线兜底仍在） |

不配置不阻塞，`nlu_provider` 与 `/health` 如实标记——与 Java 认知一致。

### 2.6（契约保持 3.1~3.4）—— **全部接受**

| # | 结论 | 说明 |
| --- | --- | --- |
| 3.1 可选字段「缺省即默认」 | 保持 | 见第 1 节锁定表；Python 不引入「必需但可空」第三形态 |
| 3.2 403 message 字样稳定 | 保持 | 现行表述含「HTTP 403」，回归测试锁定 |
| 3.3 错误码表（10 码）与错误包结构稳定 | 保持 | 变更前必先同步 Java；兜底 INTERNAL_ERROR 不产生未捕获异常 |
| 3.4 /health 结构与 OpenAPI 快照 | 保持 | 接口变更后执行 `scripts/export_openapi.py` 重导并同步 Java（流程既有） |

### 2.7（知悉项 4.1~4.4）—— **知悉，无需动作**

- `element_catalog` 多带 `color` 字段：pydantic 默认忽略额外字段，首轮联调实测已通过；
- 存储写路径 Python 无感知（R-02 口径不变）；
- 远程推理平台联调线独立：Python RemoteEngine 工作口径协议待 FR-5.4 固化（可变点仅 `_tile_to_b64`/`_parse_probs`，设计 V2.6 注记⑥）；
- Java L2 模型版本指向 `v0.0-fake`：与 infer-service 当前挂载版本一致，真实模型激活后 Python 无感知。

## 3. Java 接口文档 V1.4 变动确认

`/api/v1/nl-task/parse` 响应 `unsupported` 由字符串数组改为对象数组 `[{raw, reason}]`——**与 Python 真实契约（cv-common `UnsupportedItem`）完全一致，Python 侧无需任何改动**。Python 实测响应（2026-09-12 接口文档采集）：`"unsupported": [{"raw": "水稻田", "reason": "该要素暂不支持（可识别类别：building、forest）"}]`。

## 4. 任务「Python 侧配置 Java 服务地址」的结论：无需配置

**Python 三服务当前不存在任何主动调用 Java 的路径**，故无需在 Python 侧配置 Java 服务地址：

| 交互面 | 方向/机制 | 依据 |
| --- | --- | --- |
| 分析/计算请求 | Java → Python（Feign 直连，地址在 Java 侧 `PYTHON_*_HOST/PORT`） | 三服务为纯被调方 |
| 影像获取 | Python 按**请求入参中的预签名 URL** 直取（URL 是数据不是配置） | 评审 P-03「零配置零令牌」；URL 形态由 Java 侧 `storage.local.internal-base-url` 参数化（B-1，配置责任在 Java） |
| 结果返回 | 蒙版 base64 / 统计 JSON 随响应返回 | R-02 口径 |
| 健康/能力聚合 | Java 主动取 `/health` | B-3 |

为"未来可能用到"预留一组 Java 地址参数属于死配置，且会误导运维认知调用关系，故不引入。**例外预留**：未来若出现真实的 Python→Java 场景（进度主动回调、存储直读等），届时按「每服务 host+port 独立环境变量」（如 `JAVA_STORAGE_HOST/PORT`）补充，比现在空挂参数准确。

## 5. 随本回复的 Python 侧动作（已完成）

1. 本回复文档；
2. 回归测试补充：锁定「显式 null → 422 detail[]」「403 message 含 403 字样」两条契约注意点（见第 1 节锁定表）；
3. 无代码/配置变更——现有部署形态（三容器接入 mapchange_default + 别名直连 + 同源令牌）即为联调目标形态。
