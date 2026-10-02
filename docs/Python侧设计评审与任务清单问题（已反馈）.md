# Python 侧设计评审与任务清单问题（待确认）

| 项目 | 内容 |
| --- | --- |
| 编写日期 | 2026-09-11 |
| 评审对象 | Python推理计算服务详细设计文档 V2.4（下称"设计"）、Python服务开发任务清单 V2.5（下称"清单"） |
| 评审依据 | 影像变化检测后端推理服务需求分析文档 V2.6（下称"需求"）、Java业务服务接口文档 V1.1 |
| 总体结论 | 设计方案与任务分解**总体合理**，与需求口径一致（三级解析职责在 Java、业务参数逐请求下发、URL 完整性、逐请求降级、懒矢量化、无状态服务等均落实到位）。以下为需修订或确认的 **7 项设计问题 + 4 项清单问题**，请逐项确认。 |
| 反馈说明 | 需求/设计方已逐条反馈（2026-09-11）：各条目下方“**反馈结果**”行为结论。11 项中 10 项接受（D-01 采方案①、D-03 采告警路径）、1 项无需修改（D-07 已在 J-09 覆盖）；接受项已落实（2026-09-11），落实后版本链——Python 设计 V2.5 / Python 清单 V2.6 / 需求 V2.7 |

---

## A. 设计文档评审问题

### D-01 【契约缺口，需确认】/infer 请求体缺少 geo_extent，PNG 输入无法计算 area_m2

- **现象**：设计 §4.1 SegmentationRequest 字段为 `image_url / model_name / model_version / elements / class_mapping / colors / min_area`，**无 geo_extent 或 geo_transform**；§4.2 change-detection 请求同样没有。但两处响应的 `statistics` 均含 `area_m2`，且 §3.2.1 明确"瓦片拼接图无内嵌 CRS，**以 Java 侧校验过的 geo_extent 为准**"——请求体却没有承载它的字段，前后矛盾。
- **影响**：前端瓦片拼接图为普通 PNG（无内嵌 transform），infer-service 得不出 `geo_transform`，`area_m2` 只能返回 null；而 Java 实测响应中 PNG 输入已产出 `area_m2`。compute 侧 §4.3/4.4 请求的 `geo_transform` 同样面临"PNG 场景来源缺失"。
- **建议方案（推荐）**：`/infer/segmentation`、`/infer/change-detection` 请求体**新增 `geo_extent`（[minx,miny,maxx,maxy]，CGCS2000 经纬度）**；loader 解码后若无内嵌 transform，由 Python 按 `geo_extent + 图像宽高` 线性推导 geo_transform（推导逻辑放 cv-common，与 tile_range_to_extent 同源），并随响应返回供 Java 透传给 compute 侧。
- **需确认**：① 是否接受该方案；② 还是改由 Java 推导好 geo_transform 随请求下发（Python 完全不感知 geo_extent）。二者选一，需同步修订设计 §3.2.1/§4.1/§4.2 与清单 P-002。

**反馈结果**：接受，采方案①。`/infer/segmentation` 与 `/infer/change-detection` 请求体新增 `geo_extent`（[minx,miny,maxx,maxy]，CGCS2000 经纬度，必填——PNG 场景由 Java 透传前端入参；GeoTIFF 场景亦下发并用于与内嵌 transform 交叉校验）；cv-common geo.py 新增 `extent_to_geo_transform(geo_extent, width, height)` 线性推导（与 tile_range_to_extent 同源），loader 解码无内嵌 transform 时调用，推导结果随响应 `geo_transform` 返回、由 Java 透传 compute 侧（§4.3/§4.4 契约不变）。不采方案②的依据：geo_transform 是像素级仿射参数，属影像处理内部表示，由 Java 推导下发会让业务层感知推理内部细节，违背两侧分层（Java 只透传业务参数），且推导放 cv-common 可在 infer/compute 间复用。修订范围：设计 §3.2.1/§4.1/§4.2 + 清单 P-002。

### D-02 【自相矛盾，需确认】/nlp/parse 契约与"完全一致"表述不符

- **现象**：设计 §4.5 称 nlp 接口"请求/响应与需求 7.4 完全一致"（即请求仅 `text`），但 §2.5 编排签名 `parse(text, element_catalog)` 与清单 P-007 均要求传入 `element_catalog`；且 FR-9.8 要求响应含 `uncertain_fields`（需求 7.4 示例未列出，Java 侧实测响应已包含 `"uncertain_fields": null`）。
- **建议**：`NlParseRequest` 显式增加 `element_catalog`（必填，由 Java 从 config-service 取出随请求下发）；`NlParseResponse` 显式列出 `uncertain_fields`（可空）。同步修订设计 §4.5 表述与清单 P-003。
- **需确认**：要素**别名/修饰语词表**（如"建筑群"→building、"雪山"→snow，§3.4.1 ② 要求"别名/修饰语归一"）维护在哪里——a) Python nlp-service 内置词表；b) 作为 element_catalog 的扩展字段由 Java 下发。建议 a) 内置起步（别名属 NLP 归一化逻辑，变化频率低），后续按需再外移。

**反馈结果**：接受。① 契约修订：`NlParseRequest` 显式增加 `element_catalog`（必填，Java 从 config-service 要素目录取出随请求下发），`NlParseResponse` 显式列出 `uncertain_fields`（可空）；§4.5“与需求 7.4 完全一致”的表述改为字段级契约列举，消除歧义。② 别名/修饰语词表采方案 a（nlp-service 内置）：别名归一与句式模板、地名词典同属 NLP 语义归一逻辑，同源维护、变化频率低；若由 Java 下发，config-service 需背负 NLP 词表的运维与版本职责、每次解析多一次跨服务数据传递，收益不抵成本。后续词表确需运营化时再外移为配置。修订：设计 §4.5 + 清单 P-003。

### D-03 【能力缺口，需确认】配准校验/自动配准（FR-1.2、FR-7.7、auto_align）无对应设计模块

- **现象**：需求 FR-1.2（P1）要求"几何偏差超阈值时告警或自动配准（可选开关）"，FR-7.7 要求差异比对沿用该校验；Java 对外接口已暴露 `auto_align` 参数。但设计中 Python 侧无任何配准模块——loader 只做 CRS 校验，differ 仅有"边界腐蚀抑制伪差异"（属结果端缓解，非配准校验）。清单中亦无对应任务。
- **背景**：需求第 12 章将"配准能力"列入 M6（按需），即需求侧允许延后；但 `auto_align` 参数已在 Java 接口上线，前端可传。
- **需确认**：① 本期是否**裁剪**配准能力（Java 侧收到 auto_align=true 时如何应答——忽略并标记？还是拒绝？）；② 若保留**告警路径**（轻量），建议 infer-service 增加相位相关（phase correlation）偏差估计：偏差超阈值 → 返回专门错误/警告码，由 Java 映射为 ALIGNMENT_FAILED（422，需求 7.7 已有）；自动配准（重采样对齐）延后至 M6。

**反馈结果**：接受②，保留告警路径，决策如下：本期实现**配准校验（告警）**，自动配准（重采样对齐）延后至 M6。落地口径——infer-service **变化检测链路**（双图同请求到达）编排内置相位相关（phase correlation）整体平移估计（轻量，CPU 毫秒级）；双期比对/多期链路两期影像**分请求**到达 infer-service，像素级平移估计需引入配对机制，本期以 Java 侧 geo_extent 一致性校验（FR-8.7，已实现）兜底范围级偏差，像素级校验随 M6 自动配准一并落地。变化检测链路估计偏差超阈值（L1 配置项，默认 4px）→ 抛 `AlignmentFailedError`（内部码 ALIGNMENT_FAILED，§4.6 补行，HTTP 422），Java 映射对外 ALIGNMENT_FAILED（需求 7.7 已有该码）；`auto_align` 参数照常透传并在响应中记录 `estimated_shift_px` / `auto_aligned=false`——超阈值时**不静默忽略**（静默会让用户误以为已纠偏，属错误应答），统一返回 ALIGNMENT_FAILED 并在 message 注明“自动配准能力本期未启用（M6 交付）”。依据：FR-1.2 为“告警**或**自动配准（可选开关）”，实现告警路径即满足 P1 要求且成本远低于自动配准；若整体裁剪，Java 接口已上线的 `auto_align` 参数将无后端语义，验收存在争议风险。修订：设计 §2.3 骨架补 alignment 模块、§3 增补配准校验小节、§4.6 补码 + 清单增补 M3 任务（见 T-01）。

### D-04 【文档缺陷，建议修订】§4.6 错误码表与 errors.py 骨架漏 LOCATION_UNRESOLVED

- **现象**：§3.4.1 ③ 明确"意图与位置均缺失 → LOCATION_UNRESOLVED"，但 §4.6 错误码表、§2.2 errors.py 七个具体异常（清单 P-004 亦写"七个"）均**未收录**该错误码；上游需求 7.7 统一错误码表同样未收录（FR-9.8 正文有提及）。
- **建议**：errors.py 增加 `LocationUnresolvedError`（清单 P-004 改为"八个具体异常"），§4.6 补行；HTTP 状态建议 **422**（语义：无法处理的内容）或 400，需确认；同时建议需求 7.7 补录该码，保持三处一致。

**反馈结果**：接受。errors.py 增加 `LocationUnresolvedError`，HTTP 状态取 **422**（请求内容可理解但无法定位到可执行地理范围，语义即“无法处理的内容”，与 nlp 侧表示服务不可用的 503 NLU_UNAVAILABLE 明确区分）；§4.6 错误码表补行，清单 P-004 改为“**九个**具体异常”（评审所提 LocationUnresolvedError 之外，D-03 决策新增 AlignmentFailedError 一并计入）。需求 7.7 统一错误码表由我方同步补录 LOCATION_UNRESOLVED（422），三处保持一致。

### D-05 【口径不一，建议统一】内部 EXTENT_MISMATCH 与对外 GEO_EXTENT_MISMATCH 命名分歧

- **现象**：设计 §4.6 内部码 `EXTENT_MISMATCH`（422），需求 7.7 对外码 `GEO_EXTENT_MISMATCH`（400）。虽经 Java PythonErrorDecoder 映射、不影响功能，但命名相近易混淆（EXTENT_MISMATCH 实际语义是"双期影像尺寸/范围不一致"，GEO_EXTENT_MISMATCH 是"多期地理范围不一致"，场景亦有重叠）。
- **建议**：内部码直接更名 `GEO_EXTENT_MISMATCH`，与对外一致，减少映射歧义；HTTP 状态取 400 或 422 之一对齐。不影响骨架开发，可在 M2 实现 errors 时一并落地。

**反馈结果**：接受。内部码 `EXTENT_MISMATCH` 更名 `GEO_EXTENT_MISMATCH`，与对外码一致；HTTP 状态对齐对外口径取 **400**，使 Java PythonErrorDecoder 对该码为恒等映射、消除映射歧义。“双期像素尺寸不一致”与“多期地理范围不一致”两场景共用一码，具体差异由 message 承载。随 M2 errors 实现（清单 P-004）一并落地。

### D-06 【实现提示，非文档缺陷】推理并发信号量与同步路由的事件循环冲突

- **现象**：设计 §5 并发控制为 `asyncio.Semaphore(N)`，而 §2.3/2.4 路由均为同步 `def`（FastAPI 线程池执行）。同步工作线程中直接 `await`/`acquire` asyncio 信号量存在跨事件循环问题。
- **建议**：实现时（P-018）将 concurrency.py 落地为 `threading.Semaphore`，或将推理路由改为 `async def` + `run_in_threadpool`/进程内执行器。设计文档可在 §5 补一句实现口径，避免 M3 时返工。

**反馈结果**：接受。设计 §5 补实现口径：推理路由保持同步 `def`（由 FastAPI 线程池执行），并发控制落地为 `threading.Semaphore(N)`，消除同步工作线程与 asyncio 事件循环的跨循环问题；清单 P-018 按此口径实现。

### D-07 【提示】Java→Python 调用超时档需按任务规模设计

- **现象**：设计 §3.1.3 的 25s 超时是 **Python→通用推理服务** 单次调用口径；而 **Java→infer-service** 的整体调用在大图 CPU 场景下可达 10~15 分钟（需求 8.1：4096² CPU ≤15min）。设计未向 Java 侧给出该提示。
- **处理**：已纳入《Python推理计算服务对Java侧服务接口需求文档》J-09，设计文档本身无需修改。

**反馈结果**：确认，设计文档不修改。Java→infer-service 调用超时按任务规模分档已在《Python推理计算服务对Java侧服务接口需求文档》J-09 覆盖：同步小图短超时、大图/多期走异步队列轮询（不受单次 HTTP 超时约束）、TaskWorker 内调用 infer 的超时按需求 8.1 上限（4096² CPU ≤15min）配置。

### 设计评审通过项（无问题，不再单列确认）

三级提供方解析判定权在 Java / Python 接收 provider 提示（FR-5.6/5.7）；结果存储零配置（预签名 URL 直取、蒙版 base64 回传）；业务策略参数逐请求下发（FR-10 责任划分）；URL 完整性约束落地（约束 #10）；NLP 逐请求降级 + 熔断 + 部分成功（FR-9.7/9.8）；compute 懒矢量化（V1.2）；无状态与水平扩展；CPU/GPU 双镜像策略。以上评审结论为**合理**。

---

## B. 任务清单评审问题

### T-01 【缺口，随 D-03 确认】清单无配准能力任务

FR-1.2/FR-7.7 在清单中无对应任务项。若 D-03 确认保留告警路径，建议在 M3 增补一项（相位相关偏差估计 + ALIGNMENT_FAILED 上报）；若确认裁剪，建议在清单中显式标注"配准能力本期裁剪（对应需求 M6 按需）"，避免后续验收争议。

**反馈结果**：接受（随 D-03 决策保留告警路径）。清单 M3 增补任务“配准偏差估计（相位相关）+ AlignmentFailedError 上报”（编号插入 P-019 前，依赖 P-009 影像加载链路），验收口径：构造已知平移量的双期测试图——偏差在阈值内正常出结果且响应含 estimated_shift_px，偏差超阈值返回 ALIGNMENT_FAILED（422）、message 注明自动配准未启用。

### T-02 【验收风险，建议修订】P-022 验收依赖真实地理编码服务

P-022 验收标准"'成都金牛区xx街道'返回街道级 bbox"依赖外部服务真实可用，内网离线/无 key 环境无法执行。**建议**验收改为：Nominatim 本地 fixture（或 respx 打桩）验证三适配器协议正确性与多候选解析，真实服务联调单列为可选验收。

**反馈结果**：接受。P-022 验收改为：以 Nominatim 本地 fixture（或 respx 打桩）验证高德/百度/Nominatim 三适配器的协议正确性、多候选解析与置信度回填；真实地理编码服务联调单列为可选验收项，并标注前置条件（外网或内网自部署 Nominatim + 行政区划数据可用）。

### T-03 【外部依赖，建议标注】P-014/P-029 依赖真实模型跨团队交付

P-014（真实分割模型接入）与 P-029（INT8 量化脚本）依赖模型团队交付 FP32/INT8 双产物（需求 14.7）。清单依赖列未体现该外部依赖。建议：① P-014/P-029 依赖列补注"依赖真实 ONNX 模型交付"；② 维持 P-012 伪模型链路作为联调主线（清单已覆盖，安排合理）。

**反馈结果**：接受。P-014/P-029 依赖列补注“依赖模型团队交付真实 FP32/INT8 ONNX 模型（需求 14.7）”并标注外部依赖属性；P-012 伪模型链路维持联调主线不变——真实模型未交付不阻塞 M2/M3 其余任务，交付后由 P-014/P-029 收口。

### T-04 【核对结论】FR 覆盖核对——除配准外全覆盖，分解与依赖合理

- P0 级 FR（FR-1.1/1.3/1.4/1.6/1.7、FR-3.2/3.3 支撑、FR-5.x、FR-6.x、FR-7.1~7.5、FR-10.7）均有对应任务；
- P1/P2 级中：FR-1.5/1.9（P-016/P-017）、FR-3.5（P-014）、FR-5.5/5.7（P-013）、FR-7.6（P-020）、FR-9 全族（P-021~024）均覆盖；**唯一缺口为 FR-1.2 配准（见 T-01）**；
- 依赖关系可行：P-019/P-020（compute）仅依赖 M1+P-009，可与 infer 管线（M2/M3）并行推进，无隐性串行瓶颈；M1 骨架验收口径（schema 完整 + 实现留空）与设计 §9 一致；
- M4（nlp）按需求定位整体"按需"裁剪不影响主链路，划分合理。

**反馈结果**：核对结论认可，无修订。FR-1.2 配准缺口随 T-01 增补任务后，FR 覆盖即完整。

---

*本文件为评审待确认清单，确认结论后：D-01/D-02/D-04/D-05 涉及设计文档与清单修订，D-03/T-01 涉及范围裁剪决策，确认后可转入 M1 开发。*
