# Python 推理计算服务部署文档（内网私有云）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V2.1 |
| 编写日期 | 2026-09-12；V2.0 修订 2026-09-21（保姆级步骤细化，配置/编排范例独立成 `deploy/` 文件）；V2.1 修订 2026-10-02 |
| 适用范围 | 影像地图比对系统 Python 推理计算服务群（infer-service / compute-service / nlp-service）内网部署 |
| 前置阅读 | 《Python推理计算服务接口文档.md》（契约）、《模型交付流水线.md》（模型放置规范） |
| 部署基线 | 需求 V2.8 / 设计 V2.6 / 清单 V2.6；M1~M5 全部完成，首轮 Java 全链路联调已通过 |
| 配套文件 | `deploy/.env.example`（配置范例）、`deploy/docker-compose.prod.yml`（生产编排范例） |

**V2.0 修订记录**

| 版本 | 日期 | 修订 |
| --- | --- | --- |
| V2.1 | 2026-10-02 | §9.1 新增 provider 生效链路说明（GPU 三要件、取值校验、自助核验，bug-2026-09-28 Q1）；§8 补 `/infer/models` 模型发现校验方式 |
| V2.0 | 2026-09-21 | 全文细化为保姆级步骤；配置范例独立为 `deploy/.env.example`；生产编排独立为 `deploy/docker-compose.prod.yml`；新增 §2 模型目录规划与放置命令、§7 模型版本新增/切换流程、§9 逐步验证命令 |

---

## 0. 五分钟速查（熟练运维）

```bash
# ① 构建镜像（内网离线 pip 源注入）
docker compose build --build-arg PIP_INDEX_URL=http://<内网制品库>/simple

# ② 放置模型（生产路径 /data/models，结构见 §2）
sudo mkdir -p /data/models/landcover-seg/v2.0 /data/models/change-detection/v1.2
sudo cp <交付包>/fp32.onnx <交付包>/int8.onnx /data/models/landcover-seg/v2.0/
sudo cp <交付包>/fp32.onnx <交付包>/int8.onnx /data/models/change-detection/v1.2/

# ③ 准备配置（修改 INTERNAL_TOKEN 与模型版本号两处必填项）
cp deploy/.env.example .env && vi .env

# ④ 启动（形态 A：与 Java 同网络；--project-directory . 不可省略，见 §6）
docker compose --project-directory . -f deploy/docker-compose.prod.yml up -d

# ⑤ 验证
curl -s http://localhost:8001/health && curl -s http://localhost:8002/health && curl -s http://localhost:8003/health
```

任何一步不符合预期，回到对应章节按保姆级步骤逐项排查。

---

## 1. 部署形态与前置条件

### 1.1 服务清单与端口

| 服务 | 容器名 | 端口 | 资源建议 | Java 网络别名 |
| --- | --- | --- | --- | --- |
| infer-service | python-infer-service | 8001 | ≥8 核 CPU / ≥8GB 内存（GPU 版另需 NVIDIA 卡 + Container Toolkit） | `infer-service` |
| compute-service | python-compute-service | 8002 | ≥2 核 / ≥2GB | `compute-service` |
| nlp-service | python-nlp-service | 8003 | ≥1 核 / ≥1GB | `nlp-service` |

### 1.2 部署形态（二选一）

- **形态 A（推荐）：与 Java 同网络容器化**——三服务加入 Java compose 的 `mapchange_default` 外部网络并注册别名，Java 默认地址（`http://infer-service:8001` 等）零改动直连；
- **形态 B：独立宿主机/独立网络**——Java 侧用 `PYTHON_INFER_HOST/PORT`、`PYTHON_COMPUTE_HOST/PORT`、`PYTHON_NLP_HOST/PORT` 指向 Python 宿主机地址（Java 侧 compose 已参数化）。

> Python 三服务**不主动调用 Java**（影像经请求中的预签名 URL 直取、结果随响应返回），
> 因此 Python 侧无需配置任何 Java 地址；需要保证的是 **Python 容器能访问 URL 所指的存储地址**
> （local 存储模式下 Java 侧 `STORAGE_INTERNAL_BASE_URL` 须为 Python 容器可达地址，详见 §10.3）。

### 1.3 前置条件自检（逐条执行）

```bash
# ① Docker 与 compose 版本（要求 Docker 20.10+，compose v2）
docker --version && docker compose version

# ② Java 侧外部网络已存在（形态 A 必需；无输出说明 Java 侧尚未启动，需先部署 Java 或改形态 B）
docker network ls | grep mapchange_default

# ③ 三服务端口未被占用（无输出即空闲）
ss -tlnp | grep -E ':(8001|8002|8003)\b'

# ④ （仅 GPU 部署）NVIDIA 驱动与 Container Toolkit
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-runtime-ubuntu22.04 nvidia-smi

# ⑤ 内网 pip 制品库可达（镜像构建依赖）
curl -sI http://<内网制品库>/simple/ | head -1
```

| 项 | 要求 |
| --- | --- |
| 网络 | 与 Java 微服务群容器网络互通；不暴露公网端口；不接入 Nacos（约束 #9） |
| 镜像构建 | 内网离线 pip 源经构建参数 `PIP_INDEX_URL` 注入；docker.io 不可达时基础镜像经内网镜像仓库/加速器拉取（见 §11） |
| 模型文件 | 按 §2 放置（联调可用仓库自带伪模型 v0.0-fake） |
| 鉴权 | 与 Java 侧同一 `AUTH_ENABLED` / `INTERNAL_TOKEN`（FR-10.7，§4） |

---

## 2. 模型文件放置（部署前置，★重点）

### 2.1 目录规划

生产统一使用宿主机目录 **`/data/models`**，以只读方式挂载进 infer-service 容器的 `/models`。
容器内按 `{模型名}/{版本号}/{fp32,int8}.onnx` 三级结构组织：

```
/data/models/                        # 宿主机模型根目录 → 容器内 /models
├── landcover-seg/                   # 语义分割模型（MODELS__SEGMENTATION__NAME）
│   └── v2.0/                        # 版本（MODELS__SEGMENTATION__VERSION）
│       ├── fp32.onnx                # GPU 用 FP32（FR-3.2）
│       └── int8.onnx                # CPU 用 INT8 量化版
└── change-detection/                # 变化检测模型（MODELS__CHANGE_DETECTION__NAME）
    └── v1.2/                        # 版本（MODELS__CHANGE_DETECTION__VERSION）
        ├── fp32.onnx
        └── int8.onnx
```

**铁律**：
- 每个版本目录下 `fp32.onnx` 与 `int8.onnx` **双产物必须齐备、文件名固定**（代码按固定文件名查找）；缺 int8 → CPU 路径报 `MODEL_NOT_READY`；
- 版本目录名与 `.env` 中的 `MODELS__*__VERSION` 严格一致（区分大小写）；
- 模型名与 `.env` 中的 `MODELS__*__NAME` 严格一致。

### 2.2 放置命令（按交付形态三选一）

**形态甲：模型团队已交付双产物**（含 fp32.onnx + int8.onnx 的交付包）

```bash
# 以语义分割模型 v2.0 为例（变化检测模型同理，目录换成 change-detection/v1.2）
sudo mkdir -p /data/models/landcover-seg/v2.0

# 假设交付包解压在 ~/model-delivery/landcover-seg/v2.0/
sudo cp ~/model-delivery/landcover-seg/v2.0/fp32.onnx /data/models/landcover-seg/v2.0/
sudo cp ~/model-delivery/landcover-seg/v2.0/int8.onnx /data/models/landcover-seg/v2.0/
```

**形态乙：只交付了 FP32，需本地量化 INT8**

```bash
sudo mkdir -p /data/models/landcover-seg/v2.0
sudo cp ~/model-delivery/fp32.onnx /data/models/landcover-seg/v2.0/

# 在仓库目录执行量化（产出同目录 int8.onnx，附精度比对报告，验收口径 ≤2%）
# 注意：脚本内量化结果写入 fp32.onnx 同目录，需对该目录有写权限
.venv/bin/python scripts/quantize_int8.py /data/models/landcover-seg/v2.0/fp32.onnx
# 输出示例：[ok] 量化产物：.../int8.onnx（98.2MB → 24.6MB，压缩 4.0x）
#          [report] 最大相对误差 0.813%（阈值 ≤2%）：通过
# 退出码非 0 = 精度损失超阈值，须在业务测试集复核后再上线（需求 8.2）
```

**形态丙：联调/演示用仓库自带伪模型**（v0.0-fake，确定性语义：绿→forest、红→building）

```bash
# 伪模型已随仓库生成于 services/infer-service/models/（若被清理可重新生成）：
.venv/bin/python scripts/make_fake_models.py

# 方式一（推荐）：软链/复制到生产目录，保持 .env 指向 v0.0-fake
sudo mkdir -p /data/models
sudo cp -r services/infer-service/models/landcover-seg /data/models/
sudo cp -r services/infer-service/models/change-detection /data/models/

# 方式二：不改 /data/models，直接把 compose 挂载源改指仓库目录：
#   volumes: ["./services/infer-service/models:/models:ro"]
```

> ⚠️ 若交付包目录层级与约定不一致（例如 `yolo11/v1.0/fp32/fp32.onnx` 多一层），
> 以上述 `cp` 命令摊平到 `{name}/{version}/fp32.onnx` 两级结构即可，**不要改代码或文件名约定**。

### 2.3 放置后校验

```bash
# ① 树形结构核对（应看到每个版本目录下恰好 fp32.onnx + int8.onnx 两个文件）
find /data/models -type f -name "*.onnx" | sort
# 预期示例：
# /data/models/change-detection/v1.2/fp32.onnx
# /data/models/change-detection/v1.2/int8.onnx
# /data/models/landcover-seg/v2.0/fp32.onnx
# /data/models/landcover-seg/v2.0/int8.onnx

# ② 权限（容器以非 root 运行，须保证可读）
sudo chmod -R a+rX /data/models

# ③ （启动容器后）容器内可见性核对
docker exec python-infer-service ls -R /models
```

### 2.4 挂载方式说明（compose 中的对应关系）

`deploy/docker-compose.prod.yml` 中 infer-service 的挂载声明：

```yaml
volumes:
  - /data/models:/models:ro     # 宿主机源 : 容器内目标 : 只读
```

三要素对应关系：宿主机 `/data/models`（§2.1 放置处）→ 容器 `/models`（`.env` 中 `MODELS__DIR=/models` 指向处）→ 代码按 `MODELS__DIR/{name}/{version}/fp32.onnx` 拼路径读文件。
**三者任一改动须同步另两者**；建议三者均保持默认值不动。

---

## 3. 镜像构建（内网离线）

```bash
cd GIS-ImageCompareSystem-Inference

# ① CPU 三镜像（infer cpu + compute + nlp），内网 pip 源经构建参数注入
docker compose build --build-arg PIP_INDEX_URL=http://<内网制品库>/simple

# ② 构建结果核对
docker images | grep -E "gis-imagecomparesystem-inference|map-change-infer"
# 预期三个镜像：
# gis-imagecomparesystem-inference-infer-service
# gis-imagecomparesystem-inference-compute-service
# gis-imagecomparesystem-inference-nlp-service
```

**（可选）GPU 版 infer 镜像**：

```bash
docker build -f services/infer-service/Dockerfile.gpu \
  --build-arg PIP_INDEX_URL=http://<内网制品库>/simple \
  -t map-change-infer:gpu .
```

> 内网无法直连 docker.io 时，基础镜像需经加速器拉取后本地 tag（见 §11 排坑表）。

---

## 4. 配置文件（.env）

配置范例已独立为 **`deploy/.env.example`**，复制后修改：

```bash
cp deploy/.env.example .env
vi .env
```

**必填修改项**（只有两类，其余均可保持默认）：

| 配置项 | 说明 |
| --- | --- |
| `INTERNAL_TOKEN` | 与 Java 侧共享密钥**逐字一致**（FR-10.7）；`AUTH_ENABLED` 两侧同值 |
| `MODELS__SEGMENTATION__VERSION` / `MODELS__CHANGE_DETECTION__VERSION` | 指向 §2 实际放置的版本目录名（联调用伪模型则填 `v0.0-fake`） |

按需修改项速查：

| 场景 | 配置项 |
| --- | --- |
| GPU 服务器 | `SERVICE__DEFAULT_PROVIDER=local-gpu` |
| 走远程推理平台 | `REMOTE__ENDPOINT=http://<平台完整端点>`（留空=未配置，正常形态） |
| 配置内网 LLM | `NLP__LLM_URL=http://<完整 chat completions 端点>` |
| 配置自部署 Nominatim | `NLP__GEOCODER_URL=http://<完整端点>`（留空=回退地名词典，FR-9.8） |

配置机制说明：pydantic-settings，嵌套分隔符 `__`；URL 类配置项须含 `http(s)://` 与完整路径（启动即校验，非法拒绝启动）；`AUTH_ENABLED`/`INTERNAL_TOKEN` 为顶层扁平变量（嵌套别名不可靠，设计 V2.6 注记⑤）。完整字段说明见 `deploy/.env.example` 内注释。

---

## 5. 编排文件（docker compose）

生产编排范例已独立为 **`deploy/docker-compose.prod.yml`**（形态 A：与 Java 同网络），
与仓库根目录开发联调版 `docker-compose.yml` 的区别：不 build（直接用已构建镜像）、
模型挂载 `/data/models:ro`、含 `restart: unless-stopped`。

- **形态 A**：直接使用，无需修改（前提是 Java 侧 `mapchange_default` 网络已存在）；
- **形态 B**（独立宿主机）：删除文件底部与三个服务内的 `networks` 段，保留 `ports` 映射；Java 侧设置 `PYTHON_INFER_HOST=<本机 IP>` 等参数；
- **GPU 版**：按文件底部注释块替换 infer-service 服务定义（镜像换 `map-change-infer:gpu`，加 `deploy.resources` 段，`SERVICE__DEFAULT_PROVIDER=local-gpu`）。

---

## 6. 启动

```bash
# 启动（-d 后台；--project-directory . 锁定仓库根为项目目录，
# 使 env_file: .env 与相对路径解析到根目录——缺省时按 compose 文件所在 deploy/ 解析，会找不到 .env）
docker compose --project-directory . -f deploy/docker-compose.prod.yml up -d

# 容器状态核对（三个容器均应为 Up）
docker compose --project-directory . -f deploy/docker-compose.prod.yml ps

# 启动日志核对（配置非法会在此阶段报错退出，如 URL 缺 scheme）
docker compose --project-directory . -f deploy/docker-compose.prod.yml logs --tail=50

# 后续运维（以下 compose 均省略 --project-directory . -f deploy/docker-compose.prod.yml，实际执行请补全）
docker compose ... logs -f infer-service      # 跟踪日志
docker compose ... restart                    # 重启
docker compose ... down                       # 停止并删除容器
docker compose ... up -d --force-recreate     # 改 .env 后重建生效
```

---

## 7. 验证清单（逐条执行）

| # | 检查 | 命令 | 预期 |
| --- | --- | --- | --- |
| 1 | 三服务存活 | `curl -s http://localhost:8001/health`（8002/8003 同理） | 返回 JSON 且 `status=ok`（/health 恒免鉴权） |
| 2 | 提供方明示 | 上条返回中查看 `gpu_available` / `gpu_usable` / `remote_configured` | 与部署形态一致（FR-5.7）：纯 CPU 机均为 false；GPU 机 `gpu_available=true` |
| 3 | 模型目录挂载 | `docker exec python-infer-service ls -R /models` | 看到 §2 放置的 `{name}/{version}/{fp32,int8}.onnx` 结构 |
| 4 | 鉴权开启 | `curl -s -X POST http://localhost:8001/infer/segmentation -H 'Content-Type: application/json' -d '{}'` | `AUTH_ENABLED=true` 时返回 401 `UNAUTHORIZED` |
| 5 | 鉴权放行 | 上条命令加 `-H "X-Internal-Token: $INTERNAL_TOKEN"` | 返回 422（请求体缺字段，说明已过鉴权、schema 校验生效） |
| 6 | Java 网络可达 | 任一 Java 容器内：`wget -q -O- --header="X-Internal-Token: $INTERNAL_TOKEN" http://infer-service:8001/health` | 返回 `status=ok` |
| 7 | 模型就绪 | 先发一次真实推理（经 Java 网关 `POST /api/v1/feature-extraction`，小图同步），再查 8001 /health | `models.segmentation.loaded=true`，`version` 与 .env 一致（懒加载：首次推理后才 loaded） |
| 8 | 端到端 | 经 Java 网关发起一次完整要素识别任务 | 返回蒙版与统计；失败时按响应 `code` 对照接口文档 §4 错误码表定位 |

---

## 8. 模型版本新增与切换（运维高频操作）

模型切换经 Java 侧热生效，**infer-service 无需重启**（引擎按请求 `model_version` 懒加载，FR-3.5）。

**新增一个版本**（以分割模型 v2.1 为例）：

```bash
# ① 放置双产物
sudo mkdir -p /data/models/landcover-seg/v2.1
sudo cp <交付包>/fp32.onnx <交付包>/int8.onnx /data/models/landcover-seg/v2.1/
sudo chmod -R a+rX /data/models

# ② 校验
docker exec python-infer-service ls /models/landcover-seg/v2.1
# ③ 模型发现接口核对（双产物/类别表/默认版本一览，V1.1 新增）
curl -s -H "X-Internal-Token: $INTERNAL_TOKEN" http://localhost:8001/infer/models | python3 -m json.tool
```

**切换激活版本**：Java 侧 `PUT /api/v1/models/active` 热生效（默认版本随请求下发；缺省版本由 `.env` 的 `MODELS__*__VERSION` 兜底）。
旧版本目录可保留作回滚——回滚即 Java 侧切回旧版本号，无需动 Python 侧。

---

## 9. GPU 部署（可选）

1. 完成 §1.3 第④条自检（驱动 + Container Toolkit）；
2. 按 §3 构建 `map-change-infer:gpu` 镜像；
3. 按 §5 说明用 GPU compose 片段替换 infer-service（`SERVICE__DEFAULT_PROVIDER=local-gpu`）；
4. 验证：`curl -s http://localhost:8001/health` 中 `gpu_available=true` 且 `gpu_usable=true`（后者为试建会话实测口径，`false` 时查 §11 排坑表）。

GPU 会话失败会自动熔断回退 CPU 并在响应 `actual_provider=local-cpu` 如实标记（FR-5.6），服务不中断。

### 9.1 provider 生效链路（★ 改配置不生效时必读，bug-2026-09-28 Q1）

「切换推理提供方」涉及两侧配置，只改一处不会端到端生效：

```
Java 运行配置（判定方，FR-5.6）          Python infer-service（执行方）
inference.provider ──提示头──► X-Inference-Provider ──► registry 按提示选引擎
（PUT /api/v1/config/inference，热生效）   缺省时兜底：SERVICE__DEFAULT_PROVIDER（重启生效）
```

- **正常路径 Java 必带提示头**——Python 的 `SERVICE__DEFAULT_PROVIDER` 仅在提示头缺省
  （如远程熔断回放本地）时兜底。端到端切换 GPU 的入口是 **Java 侧运行配置**，Python 配置作兜底保留；
- **GPU 生效三要件**（缺一不可，缺一即静默兜底 local-cpu 并记 WARN 日志
  `gpu_unavailable_fallback_cpu`）：
  ① GPU 镜像 `map-change-infer:gpu`（CPU 镜像内无 onnxruntime-gpu，改配置无效）；
  ② 宿主机 NVIDIA 驱动 + nvidia-container-toolkit + compose GPU 预留段；
  ③ Java 侧提供方配置切换（或提示头缺省时 Python 兜底配置为 local-gpu）；
- **取值形式**：只认小写连字符 `remote` / `local-gpu` / `local-cpu`；`local_gpu` 等下划线形式
  在 Python 侧启动即拒绝（配置）/ 返回 400（提示头），不再静默兜底（V1.1 行为变更）；
- **自助核验**：`curl -s http://localhost:8001/health` 看 `gpu_available`（provider 列表存在）
  与 `gpu_usable`（试建会话通过）；推理响应/任务详情的 `actual_provider` 为实际执行口径；
- **常见误判**：前端显示 LOCAL_CPU + 任务中 CPU 占用高 = 实际就在 CPU 上跑（上述三要件未齐），
  并非配置未加载。

---

## 10. 运维说明

### 10.1 日志

stdout 结构化 JSON（含 service / task_id / trace_id 字段），由 Loki/ELK 采集：

```bash
docker compose --project-directory . -f deploy/docker-compose.prod.yml logs -f infer-service | grep task_id
```

### 10.2 多实例扩缩

三服务无状态。infer 多实例时请在其前挂反向代理/负载均衡（对 Java 透明）——
Java 保持单地址直连（避免与熔断/重试叠加放大尾部时延，联调约定 B-5）。

### 10.3 存储可达性（local 存储模式）

Java local 存储签发的下载 URL 主机部分须为 **Python 容器可达地址**
（Java 侧 `STORAGE_INTERNAL_BASE_URL`，联调约定 B-1）：
同网络容器化部署用 `http://storage-service:8084`；Python 宿主机部署用 `http://localhost:8084`。
URL 过期（存储端 403）时 Python 返回 `IMAGE_DECODE_FAILED` 且 message 含「HTTP 403」，
Java 自动重签重试一次，无需人工干预。

### 10.4 NLP 提供方

不配置 LLM/地理编码时为降级形态（规则解析 + 地名词典离线定位），
`nlu_provider=rule-based` 如实标记，前端确认环节兜底（FR-9.4/9.8）；
生产建议配置内网 LLM（OpenAI 兼容完整端点）与自部署 Nominatim（`deploy/.env.example` nlp 段）。

---

## 11. 常见问题（内网部署排坑）

| 问题 | 处置 |
| --- | --- |
| 改 `SERVICE__DEFAULT_PROVIDER` 为 local_gpu 不生效 | 三层核查（§9.1）：① 下划线形式非法（须 `local-gpu`，V1.1 起启动即拒）；② 运行的是 CPU 镜像（须换 GPU 镜像）；③ Java 提示头覆盖 Python 兜底（端到端切换走 Java 运行配置） |
| docker.io 拉取基础镜像超时 | 经内网镜像仓库/加速器拉取后本地 tag（联调实测 `docker.m.daocloud.io` 可用）；GPU 基础镜像 `nvidia/cuda:12.4.1-runtime-ubuntu22.04` 同法 |
| GPU 镜像 python 版本 | ubuntu22.04 默认 python3=3.10，本仓库 Dockerfile.gpu 已用 `python3.11 -m venv` 引导（ensurepip 被 Debian 禁用）；勿改回 apt python3-pip |
| onnxruntime CPU/GPU 冲突 | 两包同 namespace 互斥；GPU 镜像已按「先卸 CPU 版再装 GPU 版」处理 |
| WSL2 宿主机供图不通 | host.docker.internal 在 WSL2 Docker Desktop 拓扑下不通；供图/存储走容器网络（§10.3） |
| 启动即报 URL 非法 | URL 类配置项须含 `http(s)://` scheme 与完整路径（约束 #10 启动校验） |
| GPU 版 /health 显示 gpu_usable=false | 检查 NVIDIA Container Toolkit 挂载与 `deploy.resources` 段；该字段为试建会话实测（严格口径） |
| 推理报 MODEL_NOT_READY | 按 §2.3 校验：版本目录名与 .env 的 `MODELS__*__VERSION` 是否逐字一致、双产物文件名是否为 `fp32.onnx`/`int8.onnx`、`docker exec ... ls -R /models` 容器内是否可见 |
| 改了 .env 不生效 | 配置为 L1 部署配置，须 `up -d --force-recreate` 重建容器；模型版本切换除外（Java 热下发，§8） |

---

## 12. 安全基线

- 三服务仅绑定内网，不暴露公网端口；上传/拉取均为 Java 签发的时效 URL；
- `AUTH_ENABLED=false` 仅限全内网受信部署形态（FR-10.7），关闭状态经 Java capabilities 向前端明示；
- 内部令牌轮换：两侧同步更新 `INTERNAL_TOKEN` 并滚动重启（`up -d --force-recreate`）。
