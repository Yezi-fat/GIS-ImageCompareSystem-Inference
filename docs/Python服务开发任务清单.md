# Python 推理计算服务开发任务清单

| 项目 | 内容 |
|---|---|
| 版本 | V2.6 |
| 编写日期 | 2026-09-06（V2.6 更新于 2026-09-11） |
| 项目 | map-change-python（Python 3.11 + FastAPI + ONNX Runtime + OpenCV + Shapely + pyproj；monorepo：3 微服务 + cv-common 共享库） |
| 依据 | 需求分析文档 V2.7、Python 推理计算服务详细设计文档 V2.5 |
| 使用方式 | 每项任务可独立作为一个 Kimi Code 编码任务，开工时附上设计文档对应章节为上下文 |
| V2.6 修订 | Python 侧设计评审落实（评审 D-01~D-07/T-01~T-04）：① P-002/P-003 契约同步——infer 请求补 `geo_extent` 必填、nlp 请求补 `element_catalog` 必填且响应显式含 `uncertain_fields`（D-01/D-02）；② P-004 具体异常 7→9 个（ExtentMismatchError 更名 GeoExtentMismatchError，新增 LocationUnresolvedError、AlignmentFailedError）并补 `extent_to_geo_transform` 签名；③ M3 新增 P-018a 配准校验任务（相位相关 + ALIGNMENT_FAILED，D-03/T-01）；④ P-018 信号量口径改 threading.Semaphore（D-06）；⑤ P-022 验收改 fixture/打桩（T-02）；⑥ P-014/P-029 依赖列补注模型团队外部交付依赖（T-03） |
| V2.5 修订 | 第二轮评审复查：Python 侧无任务变更（R-02 决议维持响应体返回蒙版契约不变；R-03 模型下发走命名卷、不运行时拉取，均不涉及本清单任务口径）；依据文档版本同步 |
| V2.4 修订 | 评审问题清单落实（Python 侧）：P-010 明确按 Java 签发的内网预签名 URL 拉取影像（评审 P-03）；P-014 补多版本模型加载（请求携带 model_name/model_version，FR-3.5 切换链路落地，评审 P-02） |
| V2.3 修订 | URL 完整性约束（需求约束 #10）：配置项更名 `llm_base_url→llm_url`、`geocoder_base_url→geocoder_url`（完整端点）；P-017/P-021 明确 httpx 直调完整 URL；全局要求新增 URL 配置规则 |
| V2.2 修订 | NLP 降级链逐请求化（FR-9.7/9.8）：P-021 补充"schema 校验重试耗尽当次降级规则解析"；P-024 重写——含熔断器（circuit.py）、逐请求降级、地理编码部分成功（location=null）、降级事件日志、/health 熔断状态 |
| V2.1 修订 | 鉴权总开关（FR-10.7）：P-004 骨架新增 cv-common auth.py（auth_middleware 签名）；P-009 实现该中间件（security.auth_enabled=false 时放行，/health 豁免） |
| V2.0 修订 | **微服务拆分 + 脚手架优先**：M1 全部用于搭建工程架构——monorepo 目录、cv-common 包、三服务的类/函数签名完整，**实现留空**（`...` 或 `raise NotImplementedError`），可安装、可启动、契约可导出即验收；业务实现从 M2 起逐个填充 |

## 里程碑总览

| 阶段 | 目标 | 任务编号 |
|---|---|---|
| M1 | **工程脚手架**：monorepo + cv-common + 三服务骨架（schema 完整、函数签名完整、实现留空） | P-001 ~ P-008 |
| M2 | cv-common 实现 + infer-service 管线（伪模型跑通全链路） | P-009 ~ P-013 |
| M3 | 真实推理引擎（GPU/CPU/远程）+ compute-service 全量实现 | P-014 ~ P-020（含 P-018a） |
| M4 | nlp-service 全量实现（P2，按需） | P-021 ~ P-024 |
| M5 | 健壮性、性能与部署 | P-025 ~ P-029 |

---

## M1 工程脚手架（骨架优先：目录、schema、类/函数签名完整，实现留空）

> M1 统一要求：函数/方法**只有签名与 docstring**（标明 FR 编号与职责），函数体为 `...` 或 `raise NotImplementedError`；路由可返回 501 占位。例外：**cv-common 的 Pydantic schema 字段必须完整定义**（schema 即契约，是 Java 侧 diff 的事实源）；纯公式函数（tile_span 等）可直接实现。

| 编号 | 任务 | 对应需求/设计 | 依赖 | 验收标准 |
|---|---|---|---|---|
| P-001 | monorepo 结构：packages/cv-common + services/{infer,compute,nlp}-service 目录与 pyproject.toml（各服务以 `pip install -e` 依赖 cv-common）；顶层 docker-compose.yml 占位 | 设计 §2.1 | — | 目录与设计 §2.1 一致；cv-common 可被三服务 editable 安装 |
| P-002 | cv-common schemas（一）：segmentation/change/diff/vectorize 四组请求响应 Pydantic 模型**字段完整**（设计 §4.1~4.4 全字段，含 §4.1/§4.2 新增 `geo_extent` 必填与 §4.2 响应 `estimated_shift_px`/`auto_aligned`，评审 D-01/D-03） | 设计 §4 | P-001 | 模型可实例化；字段与设计文档逐一对应 |
| P-003 | cv-common schemas（二）：nlp（NlParseRequest/Response，需求 7.4 全字段——请求含 `element_catalog` **必填**、响应显式含 `uncertain_fields`（可空），评审 D-02）+ health（三服务健康响应模型，设计 §3.5 全字段） | 设计 §4.5/§3.5 | P-001 | 同上 |
| P-004 | cv-common 工具骨架：geo.py（GEOD 常量 + geodesic_area / tile_span / tile_range_to_extent / extent_to_geo_transform 签名，**tile_span 直接实现**）、imaging.py（decode_b64_png/encode_png_b64 签名）、errors.py（CvError 基类 + **九个具体异常**（含 GeoExtentMismatchError、LocationUnresolvedError、AlignmentFailedError，评审 D-03/D-04/D-05）+ register_error_handlers 签名）、auth.py（**auth_middleware 签名**，security.auth_enabled 开关 + /health 豁免，FR-10.7）、logging.py（configure_logging/task_id_middleware 签名） | 设计 §2.2 | P-001 | 编译（导入）通过；tile_span( z)==360/2**z 单测通过 |
| P-005 | infer-service 骨架：main.py（FastAPI app + cv-common 中间件/异常注册）+ config.py（L1 配置模型，设计 §6 全字段）+ routers（infer.py 三端点）+ engines（InferenceEngine Protocol + LocalOnnxEngine/RemoteEngine/registry/model_store 类与函数签名）+ pipeline（loader/tiler/preprocess/postprocess/colorize/alignment 函数签名）+ analysis（run_segmentation/run_change_detection 签名）+ core（capability/concurrency/storage_reader 签名） | 设计 §2.3 | P-002、P-004 | uvicorn 可启动；/infer/* 返回 501 占位且请求体被 schema 校验 |
| P-006 | compute-service 骨架：main.py + config.py + routers/compute.py（三端点）+ analysis（compute_diff/edge_erode/vectorize 函数签名） | 设计 §2.4 | P-002、P-004 | uvicorn 可启动；/compute/* 返回 501 占位且请求体被 schema 校验 |
| P-007 | nlp-service 骨架：main.py + config.py（nlp 配置模型，设计 §6 全字段，含 llm_timeout_s 与 circuit 熔断参数）+ routers/nlp.py（两端点）+ nlp/（parse/extract_by_llm/extract_by_rules/geocode 函数签名 + gazetteer 数据结构 + **circuit.py CircuitBreaker 类骨架**，FR-9.7） | 设计 §2.5/§6 | P-003、P-004 | uvicorn 可启动；/nlp/parse 返回 501 占位 |
| P-008 | M1 集成验收：三服务 Dockerfile（infer 含 cpu/gpu 双版骨架）+ compose 起三服务；三份 OpenAPI 快照导出至 docs/openapi/；三服务 /health 返回结构符合 schema（字段可占位） | 设计 §7/§9 | P-005~P-007 | compose 一键起三服务；/health 响应模型校验通过；骨架核对表（设计 §2 全部类/函数存在）通过 |

## M2 cv-common 实现与 infer-service 管线（伪模型）

| 编号 | 任务 | 对应需求/设计 | 依赖 | 验收标准 |
|---|---|---|---|---|
| P-009 | cv-common 实现：geo（geodesic_area/tile_range_to_extent/extent_to_geo_transform 公式）、imaging 编解码、errors 异常处理注册、**auth_middleware（true 校验 X-Internal-Token、false 放行、/health 恒豁免，与 Java 侧同一 AUTH_ENABLED 语义）**、logging JSON + task_id 透传 | 设计 §2.2、FR-10.7 | M1 | 单测覆盖；开关两态行为正确（true 无 token 返 401 / false 无 token 放行）；日志 JSON 含 service 与 task_id 字段 |
| P-010 | loader：按 **Java 签发的内网预签名 URL** 拉取影像（httpx，零配置零令牌，评审 P-03）、PNG/JPEG（OpenCV）与 TIFF/GeoTIFF（rasterio）解码、CRS 校验（拒绝投影坐标系 GeoTIFF）、统一 RGB uint8 + geo_transform | 设计 §3.2.1 | P-009 | 三种格式解码正确；投影坐标系 GeoTIFF 返回 INVALID_INPUT；过期/非法 URL 返回 IMAGE_DECODE_FAILED |
| P-011 | tiler + postprocess + colorize：256×256 重叠 32px 流式切分、概率图均值融合、形态学 + 小区域过滤、要素合成/差异蒙版着色 | FR-1.3/1.6/6.7/7.3，设计 §3.2.2~3.2.4 | P-010 | 合成大图拼接无接缝（重叠区断言）；4096² 内存占用有界 |
| P-012 | 伪 ONNX 模型 + LocalOnnxEngine 骨架跑通：伪模型制作脚本（恒等/固定类别输出）+ 引擎 Protocol 落地 + registry 选择 + /infer/segmentation 全管线（loader→tiler→engine→postprocess→colorize→统计） | FR-5.1/6.x，设计 §3.3.1 | P-011 | 伪模型端到端返回结构符合设计 §4.1；类别超映射返 UNSUPPORTED_ELEMENT |
| P-013 | capability 环境探测 + infer /health 真实化：CUDA 可用性（试建小会话）/显存（pynvml）/CPU 核数/模型加载状态 | FR-5.5/5.7，设计 §3.5 | P-012 | 无 GPU 环境 gpu_available=false 且不影响启动；字段与设计 §3.5 一致 |

## M3 真实推理引擎与 compute-service

| 编号 | 任务 | 对应需求/设计 | 依赖 | 验收标准 |
|---|---|---|---|---|
| P-014 | 真实分割模型接入：preprocess 按模型交付规范实现（归一化/通道/尺寸），session 常驻预热；**多版本加载：模型按 {name}/{version}/ 目录组织，请求可携带 model_name/model_version 按名+版本懒加载并缓存会话（FR-3.5 切换链路落地，评审 P-02）** | FR-3.2/3.5，设计 §3.1.2 | P-012；**外部：模型团队交付真实 FP32/INT8 ONNX 模型（需求 14.7，评审 T-03）** | 真实 ONNX 输出概率图维度正确；info() 返回真实类别表；请求切换 model_version 后按新版本推理 |
| P-015 | GPU/CPU 双路径：CUDAExecutionProvider（FP32）与 CPUExecutionProvider（INT8）加载策略、GPU 不可用自动回退 CPU 并如实标记 actual_provider | FR-3.2/5.2，设计 §3.1.2 | P-014 | 两路径结果一致性误差在容差内；回退标记正确 |
| P-016 | 变化检测引擎与 /infer/change-detection：双时相 Tile 批推理、概率图 + 阈值二值化 + 后处理 | FR-1.x，设计 §3.3.2 | P-014 | 返回 mask + probmap + 统计，符合设计 §4.2 |
| P-017 | RemoteEngine：httpx 调通用推理服务（**endpoint 为完整 URL，直接 POST 不拼接路径**，需求约束 #10）、Tile 批量提交（batch_size 可配）、批量失败降级逐块重试一次、25s 超时 | FR-1.9/5.2，设计 §3.1.3 | P-012 | respx 打桩验证批量/重试逻辑；超时可控 |
| P-018 | 推理信号量并发控制（**threading.Semaphore**——路由为同步 def、由 FastAPI 线程池执行，评审 D-06；CPU 2 槽/GPU 4 槽可配）+ intra_op_num_threads = 核数/槽数 | 设计 §5 | P-015 | 并发压测内存/显存不超限；排队请求不丢失 |
| P-018a | 配准校验（变化检测链路，评审 D-03/T-01）：pipeline/alignment 相位相关整体平移估计（灰度降采样 + cv2.phaseCorrelate），阈值 alignment.max_shift_px（L1，默认 4px）；超限抛 AlignmentFailedError（ALIGNMENT_FAILED/422，message 注明自动配准未启用）；响应记录 estimated_shift_px / auto_aligned=false；auto_align 透传记录不改流程（自动配准 M6） | FR-1.2/7.7，设计 §3.2.5 | P-010、P-016 | 构造已知平移量的双期测试图：阈值内正常出结果且响应含 estimated_shift_px；超限返 ALIGNMENT_FAILED（422）；未超阈值时 auto_aligned=false 如实返回 |
| P-019 | compute-service /compute/diff：三态划分（added/removed/unchanged）、边缘伪差异抑制（边界腐蚀）、分色蒙版、双单位统计（px + m²，cv-common Geod） | FR-7.2~7.5，设计 §3.3.3 | M1、P-009 | 合成用例三态面积精确可断言；响应符合设计 §4.3 |
| P-020 | compute-service /compute/vectorize：findContours → 面积过滤 → Shapely 抽稀 → 像素转经纬度 → GeoJSON + bbox + centroid + Geod 面积；差异新增/减少分开矢量化；懒调用解耦验证 | FR-1.7/6.4/7.6，设计 §3.3.4 | M1、P-009 | 合成多边形顶点/面积/bbox 精度达标；面积与 Geod 独立验算一致；单独调用与管线内调用结果一致 |

## M4 nlp-service（P2，按需）

| 编号 | 任务 | 对应需求/设计 | 依赖 | 验收标准 |
|---|---|---|---|---|
| P-021 | llm_client：httpx 直调**完整端点 `llm_url`**（OpenAI chat completions 协议请求体，不经 SDK 路径拼接，需求约束 #10）+ instructor 结构化输出（intent/location_text/elements/periods/confidence），prompt 注入要素目录；**schema 校验失败重试 2 次，耗尽后抛降级信号（不当请求错误抛出，由 parser 当次落规则解析，FR-9.7）**；单次调用超时 llm_timeout_s（默认 15s） | FR-9.1/9.3/9.7，设计 §3.4 | M1 | 标准句式解析正确；非法输出重试后进入降级路径而非报错 |
| P-022 | geocoder：高德/百度/Nominatim 三适配（配置切换 base_url），多候选返回、置信度 | FR-9.2 | P-021 | 以 Nominatim 本地 fixture（或 respx 打桩）验证三适配器协议正确性、多候选解析与置信度回填（评审 T-02）；真实地理编码服务联调单列为可选验收（前置：外网或内网自部署 Nominatim + 行政区划数据可用） |
| P-023 | rule_based 降级：jieba 分词 + 句式模板 + 地名词典，响应标记 nlu_provider=rule-based | FR-9.6 | P-021 | 固定句式可解析；LLM 不可用自动降级且明示 |
| P-024 | /nlp/parse 编排 + /health（逐请求降级版，FR-9.7/9.8）：circuit.py 熔断器（60s 窗口连续 5 次失败断流 30s 后半开，参数可配）；parser 逐请求 try LLM → 超时/5xx/重试耗尽当次落规则解析并记 structlog WARN（event=nlu_degraded, reason）；要素映射（别名/修饰语归一）、unsupported 列表、uncertain_fields 标注；**地理编码失败部分成功**（location=null + uncertain_fields 含 location，schema 允许 location 可空；仅意图与位置均缺失才返 LOCATION_UNRESOLVED）；/health 上报 nlu.circuit 状态 | FR-9.3/9.4/9.7/9.8，设计 §3.4/§3.5 | P-021~P-023 | LLM 故障注入：当次降级成功且 nlu_provider=rule-based、日志含 nlu_degraded；连续故障触发断流与半开恢复；地理编码失败返回 200 部分成功而非错误；完全不可执行时返 LOCATION_UNRESOLVED |

## M5 健壮性、性能与部署

| 编号 | 任务 | 对应需求/设计 | 依赖 | 验收标准 |
|---|---|---|---|---|
| P-025 | 输入规模限制与防御：local-cpu 模式 >2048 拒绝（INPUT_TOO_LARGE）；大图分块内存回归 | 设计 §5 | M3 | 超限明确报错；4096² 任务内存峰值在预算内 |
| P-026 | 性能基准：pytest-benchmark 建立单 Tile/1024 图 CPU 基线，纳入 CI 回归 | 设计 §8、需求 8.1 | M3 | 基线报告产出；回归劣化 >20% 报警 |
| P-027 | 测试补齐：tiler 无缝性、differ 三态、错误码映射、引擎选择矩阵（provider_hint × 环境）、cv-common 公式 | 设计 §8 | M3 | 覆盖率 ≥ 80%（cv-common 与引擎核心 100%） |
| P-028 | GPU 版镜像完善（nvidia/cuda 基础镜像 + onnxruntime-gpu + pynvml）与四镜像 CI（infer cpu/gpu + compute + nlp） | 设计 §7.1 | P-015 | 四镜像均构建通过（内网离线 pip 源）；GPU 容器 /health 报 gpu_usable=true |
| P-029 | INT8 量化脚本与模型交付流水线文档（FP32/INT8 双产物制作、模型目录规范） | FR-3.2，需求 14.7 | P-015；**外部：模型团队交付 FP32 模型与量化规范（需求 14.7，评审 T-03）** | 量化脚本可复现；量化精度损失报告（≤2%） |

---

## 全局要求（每项任务都需遵守）

1. 所有 Mat/数组及时释放，禁止内存泄漏（管线任务必查）；推理走信号量。
2. 接口变更同步更新 docs/openapi/ 三份快照，与 Java 侧契约 diff 在 CI 中校验；**schema 只改 cv-common**，三服务禁止各自重复定义。
3. 请求/响应严格符合设计文档第 4 章 schema（Pydantic 模型即契约）。
4. **M1 骨架任务禁止提前写业务实现**（纯公式函数与 schema 字段除外）；M2 起的实现任务禁止改动已验收的公开签名（确需改动时同步更新设计文档与 Java 侧契约）。
5. 测试用合成小尺寸影像 fixtures，不依赖真实模型（真实模型测试单独标记 skip 条件）。
6. **URL 类配置项一律为完整 URL**（含路径），代码不得在其后拼接固定路径（地理编码仅可按 provider 协议拼接 query 参数）；启动时校验可解析为绝对地址，非法拒绝启动（需求约束 #10）。
7. 提交信息格式：`[P-xxx] 简述`，关联 FR 编号。
