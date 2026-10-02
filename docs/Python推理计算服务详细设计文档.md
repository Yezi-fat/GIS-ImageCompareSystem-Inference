# Python 推理计算服务详细设计文档

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V2.6 |
| V2.6 修订 | **M2 实现期签名扩展与口径对齐**（开发任务 P-009~P-013）：① §2.3 `InferenceEngine.segment/detect_change` 增加可选 `model_name/model_version`（FR-3.5 按请求选版本的承载参数，评审 P-02 落地必需）；② §2.3 tiler 增加 `alloc_canvas`/`finalize_canvas`，`fuse_tile` 增可选 `count_canvas`（均值融合双缓冲）；③ `run_segmentation(req)` → `run_segmentation(req, provider_hint=None)`（透传 X-Inference-Provider；M3 起 `run_change_detection` 同口径）；④ §6 模型文件名默认对齐 §3.1.2 约定 `{name}/{version}/{fp32|int8}.onnx`（原 segmentation_fp32.onnx 等示例名与目录约定冲突）；⑤ 配置落地口径——`security.auth_enabled/internal_token` 以顶层扁平环境变量 AUTH_ENABLED/INTERNAL_TOKEN 承接（pydantic-settings 嵌套别名解析不可靠），结构化视图由 Settings.security 属性提供；⑥ RemoteEngine 线上协议为工作口径（tiles 以 PNG base64 上行、probs 以 float32 原始字节 base64 下行），与平台方固化接口规范（FR-5.4）后如不一致仅改 `_tile_to_b64`/`_parse_probs` 两处；⑦ M4 落地口径——instructor 未采用（其依赖 OpenAI SDK 客户端对象，与 httpx 直调完整 URL 的约束 #10 不兼容），结构化输出以 `response_format=json_object` + Pydantic 校验重试 2 次实现同等约束（P-021）；地理编码失败回退地名词典定位（bbox 命中时低置信返回并标注 uncertain_fields，FR-9.6 离线链路），词典未命中才 location=null（FR-9.8）；规则抽取名词候选需剔除行政区词（词典成员与"省/市/区/街道"类后缀词），防 unsupported 噪声 |
| V2.5 修订 | **Python 侧设计评审落实（评审 D-01~D-07）**：① infer 请求契约补 `geo_extent`（必填，CGCS2000；无内嵌 transform 时由 cv-common `extent_to_geo_transform` 线性推导，GeoTIFF 场景交叉校验——D-01）；② nlp 契约显式化——请求含 `element_catalog`（必填）、响应显式列 `uncertain_fields`，别名/修饰语词表 nlp-service 内置（D-02）；③ 新增配准校验告警路径（§3.2.5 相位相关平移估计 + ALIGNMENT_FAILED，变化检测链路内置，自动配准列 M6——D-03）；④ 错误码补 `LOCATION_UNRESOLVED`（422）与 `ALIGNMENT_FAILED`（422），`EXTENT_MISMATCH` 更名 `GEO_EXTENT_MISMATCH`（400，与对外码一致），errors.py 具体异常 7→9 个（D-04/D-05）；⑤ §5 并发信号量口径修正为 `threading.Semaphore`（同步路由 + FastAPI 线程池，D-06） |
| V2.4 修订 | **评审问题清单落实（Python 侧部分）**：① P-03 影像传递明确为 Java 签发的内网预签名 URL（Python 零配置零令牌，storage_reader 按 URL 直取）；② P-02 模型多版本加载落地——模型按 `{name}/{version}/` 组织，请求体新增可选 `model_name`/`model_version`（§4.1/4.2），引擎按名+版本懒加载，Java 激活切换经配置热生效随请求下发、无需重启 |
| V2.3 修订 | **URL 完整性约束落地**（对应需求 V2.3 约束 #10）：外部服务地址类配置项改为**完整 URL**——`llm_base_url` → `llm_url`（完整 chat completions 端点，httpx 直调，不经 SDK 路径拼接）、`geocoder_base_url` → `geocoder_url`、`remote.endpoint` 明确为完整推理端点；新增启动期 URL 合法性校验；自有微服务根地址为例外（路径属内部契约） |
| V2.2 修订 | **NLP 降级链细化为逐请求级**（对应需求 V2.2 FR-9.7/9.8）：① parser 编排改为"逐请求 try LLM → 超时/5xx/结构化输出重试耗尽 → 当次落规则解析"，新增进程内熔断器（circuit.py）；② 地理编码失败改为**部分成功**（location=null + uncertain_fields 标注），LOCATION_UNRESOLVED 收窄为完全不可执行场景；③ 降级事件 structlog 计数、/health 上报熔断状态；④ 配置补充 llm 超时与熔断参数 |
| V2.1 修订 | **用户鉴权总开关**（对应需求 V2.1 FR-10.7）：三服务新增 L1 部署配置 `security.auth_enabled`（默认 true，与 Java 侧同一环境变量注入）——true 时校验请求头 `X-Internal-Token`（与 Java 共享密钥，/health 豁免）；false 时跳过校验、任何内网调用方可用；校验逻辑收敛在 cv-common 鉴权中间件，三服务统一挂载 |
| V2.0 修订 | **微服务架构升级**（对应需求 V2.0）：单体 FastAPI 应用拆分为 **3 个微服务 + 1 个共享库**——infer-service（推理：分割/变化检测）、compute-service（计算：差异/矢量化）、nlp-service（NLP 解析）+ cv-common（共享 schema/坐标/影像工具/错误/日志）；第 2 章重写为 **monorepo 工程结构与各服务类/函数骨架**（实现留空，配合"骨架优先"开发模式，骨架即开发任务清单 M1 的验收依据）；部署调整为按服务独立镜像；三服务均无状态、不接入注册中心，由 Java 侧配置地址直连 |
| V1.3 修订 | 配合产品化配置体系（需求 V1.8 FR-10）：Python 侧仅保留 L1 部署配置（env 注入）；业务策略参数由 Java 运行配置持有并逐请求下发 |
| V1.2 修订 | 配合结果存储策略修订（需求 V1.7）：/compute/vectorize 支持懒调用，无状态设计天然支持 |
| V1.1 修订 | 坐标系统一为 CGCS2000 经纬度投影：面积/中心点计算改为 pyproj.Geod 大地测量计算；loader 增加 CRS 校验 |
| 编写日期 | 2026-09-06（V2.5 更新于 2026-09-11） |
| 对应需求文档 | 影像变化检测后端推理服务需求分析文档 V2.7（FR 编号与接口定义以该文档为准） |
| 服务群定位 | 无状态推理/计算/NLP 黑盒群，仅内网可达 |
| 技术基线 | Python 3.11 + FastAPI + ONNX Runtime + OpenCV + rasterio + Shapely + pyproj |

---

## 1. 设计概述

### 1.1 职责边界与拆分原则

**Python 微服务群整体负责**：影像预处理与 Tile 管线、本地推理（ONNX Runtime GPU/CPU）、远程推理调用（通用推理服务）、形态学后处理、双期差异逐像素计算、轮廓矢量化与面积/中心点计算、自然语言解析（NLU + 地理编码）、推理环境能力上报。

**Python 微服务群整体不负责**：任务概念与状态机（Java 侧）、鉴权与对外接口（仅监听内网）、业务数据库读写、对象存储写入（蒙版以 base64/二进制随响应返回，由 Java 上传 OSS/OBS）。

**拆分原则**：

1. 按**资源特征与变更频率**拆分：infer-service 吃 GPU/CPU 算力、随模型迭代；compute-service 纯 CPU 数值计算、轻量；nlp-service 依赖外部 LLM/地理编码服务、可整体裁剪（FR-9.6）。三者独立扩缩容、独立发版。
2. 三服务**均无状态**：不保存任务、不依赖上次调用，每次请求自包含，可被 Java 任意 worker 调用，支持多实例水平扩展。
3. 跨服务共享的**契约与工具**沉淀到 cv-common 库（schema 即契约，Java 侧 OpenAPI diff 以此为事实源），业务逻辑不下沉。
4. 不接入 Nacos：Java analysis-service / result-service 以配置地址直连（需求约束 #9）。

### 1.2 服务清单与职责

| 服务 | 端口 | 核心职责 | 资源特征 |
| --- | --- | --- | --- |
| **infer-service** | 8001 | /infer/segmentation、/infer/change-detection；推理引擎（本地 GPU/CPU、远程）、Tile 管线、模型管理、环境能力上报 | GPU/多核 CPU，内存大 |
| **compute-service** | 8002 | /compute/diff、/compute/vectorize；差异三态计算、矢量化、Geod 面积、蒙版分色 | 纯 CPU，轻量 |
| **nlp-service** | 8003 | /nlp/parse；NLU（LLM/规则降级）、地理编码适配、能力上报 | 轻量，依赖外部服务 |
| **cv-common**（库，非服务） | — | 请求/响应 schema（Pydantic）、CGCS2000 坐标工具、影像编解码工具、统一错误模型、日志 | 被三服务依赖 |

### 1.3 与 Java 服务的协作约定

| 约定 | 说明 |
| --- | --- |
| 影像传递 | 请求中传**对象存储内网预签名 URL**（Java 已上传并经 storage-service 签发，internal 短 TTL 默认 10min），infer-service 用 httpx 直接拉取——Python 侧零配置、零内部令牌（评审 P-03 方案①）；大图避免 base64 |
| 结果返回 | 蒙版以 PNG 二进制 base64 返回（单图层通常 < 2MB）；统计/轮廓以 JSON 返回 |
| provider 提示 | 请求头 `X-Inference-Provider: remote / local-gpu / local-cpu`，infer-service 据此选择引擎；头缺省时按本地配置默认值 |
| 降级标记 | 实际执行与请求 provider 不一致时（如本地 GPU 不可达自动降 CPU），响应 `actual_provider` 字段如实返回 |
| 任务追踪 | 请求头 `X-Task-Id` / `X-Trace-Id` 透传，写入结构化日志（cv-common 中间件统一实现） |
| 调用方 | analysis-service → infer/compute/nlp；result-service → compute（懒矢量化）；均为配置地址直连 |
| 内部鉴权 | `security.auth_enabled=true`（默认）时校验请求头 `X-Internal-Token`（与 Java 共享密钥），`/health` 豁免；`=false` 时跳过校验（FR-10.7，对应 Java 侧 `security.auth.enabled` 同一开关） |

## 2. 工程结构与各服务骨架

### 2.1 monorepo 工程总览

单仓库多服务，cv-common 以可安装包形式被三服务依赖（`pip install -e packages/cv-common`）：

```
map-change-python/
├── packages/
│   └── cv-common/                    # 共享库（见 §2.2）
│       ├── pyproject.toml
│       └── src/cv_common/
├── services/
│   ├── infer-service/                # 推理服务（见 §2.3）
│   │   ├── pyproject.toml
│   │   ├── Dockerfile                # CPU 版
│   │   ├── Dockerfile.gpu            # GPU 版
│   │   ├── models/                   # 模型文件挂载点（容器 volume）
│   │   ├── src/infer_service/
│   │   └── tests/
│   ├── compute-service/              # 计算服务（见 §2.4）
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   ├── src/compute_service/
│   │   └── tests/
│   └── nlp-service/                  # NLP 服务（见 §2.5）
│       ├── pyproject.toml
│       ├── Dockerfile
│       ├── src/nlp_service/
│       └── tests/
├── docker-compose.yml                # 三服务 + 模型卷（供 Java 侧联调）
└── docs/openapi/                     # 三服务 OpenAPI 契约快照（CI 与 Java 侧 diff）
    ├── infer.json
    ├── compute.json
    └── nlp.json
```

### 2.2 cv-common 共享库骨架

```python
# src/cv_common/
# ├── schemas/            # 请求/响应模型（Pydantic 2）——三服务契约的唯一事实源
# │   ├── segmentation.py #   SegmentationRequest / SegmentationResponse
# │   ├── change.py       #   ChangeDetectRequest / ChangeMaskResponse
# │   ├── diff.py         #   DiffRequest / DiffResponse
# │   ├── vectorize.py    #   VectorizeRequest / VectorizeResponse / RegionItem
# │   ├── nlp.py          #   NlParseRequest / NlParseResponse
# │   └── health.py       #   HealthResponse（gpu/vram/models/nlu/geocoder 字段）
# ├── geo.py              # CGCS2000 坐标工具
# ├── imaging.py          # PNG base64 编解码、二值蒙版读写
# ├── errors.py           # 统一错误模型
# ├── auth.py             # 内部令牌鉴权中间件（V2.1 新增，FR-10.7）
# └── logging.py          # structlog JSON + X-Task-Id 中间件

# ---- geo.py（V1.1 坐标系约束的代码落点）----
from pyproj import Geod

# CGCS2000 椭球参数（长半轴 a、扁率 rf）；经纬度下面积必须大地测量计算（V1.1 修正）
GEOD = Geod(a=6378137.0, rf=298.257222101)

def geodesic_area(poly) -> float:
    """椭球面面积（平方米）"""
    ...

def tile_span(z: int, span_base: float = 360.0) -> float:
    """span(z) = 360 / 2^z（度），经纬度瓦片方案（非 Web Mercator）"""
    ...

def tile_range_to_extent(z: int, x_min: int, x_max: int, y_min: int, y_max: int,
                         origin: tuple[float, float] = (-180.0, 90.0)) -> list[float]:
    """瓦片范围 → [minx, miny, maxx, maxy]（度，线性公式）"""
    ...

def extent_to_geo_transform(geo_extent: list[float], width: int, height: int) -> list[float]:
    """[minx,miny,maxx,maxy] + 图像宽高 → 6 参仿射 geo_transform（度/像素，线性推导；
    与 tile_range_to_extent 同源。供无内嵌 transform 的影像（瓦片拼接 PNG）换算面积/矢量化坐标，评审 D-01）"""
    ...

# ---- imaging.py ----
def decode_b64_png(b64: str) -> "np.ndarray": ...
def encode_png_b64(arr: "np.ndarray") -> str: ...

# ---- errors.py ----
class CvError(Exception):
    """业务异常基类：携带内部错误码与 HTTP 状态（错误码表见 §4.6）"""
    def __init__(self, code: str, message: str, http_status: int = 400): ...

class UnsupportedElementError(CvError): ...      # UNSUPPORTED_ELEMENT
class ImageDecodeFailedError(CvError): ...       # IMAGE_DECODE_FAILED
class GeoExtentMismatchError(CvError): ...       # GEO_EXTENT_MISMATCH（V2.5 更名，与对外码一致，评审 D-05）
class ModelNotReadyError(CvError): ...           # MODEL_NOT_READY
class InferenceFailedError(CvError): ...         # INFERENCE_FAILED
class NluUnavailableError(CvError): ...          # NLU_UNAVAILABLE
class InputTooLargeError(CvError): ...           # INPUT_TOO_LARGE
class LocationUnresolvedError(CvError): ...      # LOCATION_UNRESOLVED（422，意图与位置均缺失，评审 D-04）
class AlignmentFailedError(CvError): ...         # ALIGNMENT_FAILED（422，配准偏差超阈值，评审 D-03）

def register_error_handlers(app) -> None:
    """FastAPI 统一异常处理：CvError → {code, message} JSON"""
    ...

# ---- logging.py ----
def configure_logging(service_name: str) -> None:
    """structlog JSON 日志，注入 service 字段"""
    ...

def task_id_middleware(app) -> None:
    """X-Task-Id / X-Trace-Id 透传进日志上下文"""
    ...

# ---- auth.py（V2.1 新增，FR-10.7）----
def auth_middleware(app, *, enabled: bool, internal_token: str) -> None:
    """内部令牌校验：enabled=True 时校验请求头 X-Internal-Token（/health 豁免），
    失败返回 401 UNAUTHORIZED；enabled=False 时直接放行（任何人可用，对应 Java 侧同一总开关）"""
    ...
```

### 2.3 infer-service 骨架

```python
# src/infer_service/
# ├── main.py              # FastAPI 入口、中间件、异常注册（cv-common）
# ├── config.py            # pydantic-settings（L1 部署配置，§6）
# ├── routers/
# │   ├── infer.py         # /infer/segmentation、/infer/change-detection
# │   └── health.py        # /health
# ├── engines/
# │   ├── base.py          # InferenceEngine 协议（FR-5.1）
# │   ├── local_onnx.py    # LocalOnnxEngine（CUDA FP32 / CPU INT8 双路径，FR-3.2）
# │   ├── remote.py        # RemoteEngine（通用推理服务 HTTP）
# │   ├── registry.py      # 按 provider 提示选择引擎
# │   └── model_store.py   # 模型加载、版本、FP32/INT8 选择
# ├── pipeline/
# │   ├── loader.py        # 影像拉取与解码（rasterio/Pillow/OpenCV）+ CRS 校验
# │   ├── tiler.py         # Tile 切分与重叠融合拼接（FR-1.3/6.7）
# │   ├── preprocess.py    # 归一化、通道变换（随模型交付规范实现）
# │   ├── postprocess.py   # 形态学、小区域过滤（FR-1.6/7.4）
# │   ├── colorize.py      # 蒙版着色（FR-6.3/7.3）
# │   └── alignment.py     # 配准校验：相位相关整体平移估计（FR-1.2/7.7，评审 D-03）
# ├── analysis/
# │   ├── segmentation.py  # 要素识别编排（FR-6）
# │   └── change_detection.py  # 端到端变化检测编排（FR-1）
# └── core/
#     ├── capability.py    # 环境探测：CUDA/显存/CPU 核数（/health 数据源）
#     ├── concurrency.py   # 推理信号量
#     └── storage_reader.py # 按预签名 URL 从对象存储拉取影像（httpx；零配置零令牌，评审 P-03）

# ---- engines/base.py ----
from typing import Protocol
import numpy as np
from cv_common.schemas.health import ModelInfo

class InferenceEngine(Protocol):
    def segment(self, tiles: list[np.ndarray],
                model_name: str | None = None,
                model_version: str | None = None) -> list[np.ndarray]:
        """语义分割：输入预处理后的 Tile 批次，返回各类别概率图 [C,H,W]；
        model_name/model_version 缺省用默认版本（FR-3.5 按请求选版本，评审 P-02，M2 签名扩展）"""
        ...
    def detect_change(self, tiles_before: list[np.ndarray],
                      tiles_after: list[np.ndarray],
                      model_name: str | None = None,
                      model_version: str | None = None) -> list[np.ndarray]:
        """变化检测：输入双时相 Tile 批次，返回变化概率图 [1,H,W]"""
        ...
    def info(self) -> ModelInfo:
        """模型名、版本、provider、支持类别 ID 列表"""
        ...

# ---- engines/registry.py ----
def get_engine(provider_hint: str | None) -> InferenceEngine:
    """remote → RemoteEngine；local-gpu 且 GPU 可用 → GPU 会话；否则 CPU 兜底（响应标记 actual_provider）"""
    ...

# ---- pipeline/tiler.py ----
def split_tiles(image: "np.ndarray", tile_size: int = 256, overlap: int = 32):
    """流式切分（stride=tile_size-overlap），yield (tile, offset)"""
    ...
def alloc_canvas(height: int, width: int, channels: int):
    """预分配（概率和画布, 计数画布）双缓冲（M2 签名扩展）"""
    ...
def fuse_tile(canvas_prob: "np.ndarray", tile_prob: "np.ndarray", offset: tuple[int, int],
              count_canvas: "np.ndarray | None" = None) -> None:
    """重叠区概率图均值融合（和画布累加 + 计数画布，finalize_canvas 取均值），避免接缝（FR-6.7）"""
    ...
def finalize_canvas(canvas_prob: "np.ndarray", count_canvas: "np.ndarray") -> "np.ndarray":
    """概率和画布 ÷ 计数画布 → 均值概率图（M2 签名扩展）"""
    ...

# ---- analysis/segmentation.py ----
def run_segmentation(req, provider_hint: str | None = None) -> "SegmentationResponse":
    """loader → tiler → engine.segment → 融合 → 类别提取（FR-6.6 校验）→ 后处理 → 着色 → 统计；
    provider_hint 透传 X-Inference-Provider 头（M2 签名扩展）"""
    ...

# ---- routers/infer.py ----
from fastapi import APIRouter
router = APIRouter()

@router.post("/infer/segmentation")
def segmentation(req): ...            # TODO → run_segmentation(req)

@router.post("/infer/change-detection")
def change_detection(req): ...        # TODO → run_change_detection(req)

@router.get("/health")
def health(): ...                     # TODO → capability 探测汇总（§3.4 字段）
```

### 2.4 compute-service 骨架

```python
# src/compute_service/
# ├── main.py              # FastAPI 入口（cv-common 中间件/异常）
# ├── config.py            # L1 配置（端口等，极少）
# ├── routers/
# │   ├── compute.py       # /compute/diff、/compute/vectorize、/health
# │ └── analysis/
#     ├── differ.py        # 双期差异逐像素计算（FR-7.2/7.5）
#     └── vectorizer.py    # findContours → Shapely → GeoJSON（FR-1.7/6.4/7.6）

# ---- analysis/differ.py ----
def compute_diff(before_mask, after_mask, min_area: int, colors: dict) -> tuple:
    """三态划分：added=after&~before，removed=before&~after；
       边缘伪差异抑制（边界腐蚀，FR-7.7）→ 后处理 → 分色蒙版 + 统计（FR-7.4/7.5）"""
    ...

def edge_erode(mask, px: int = 1):
    """蒙版边界腐蚀，抑制配准误差导致的边缘伪差异"""
    ...

# ---- analysis/vectorizer.py ----
def vectorize(mask, min_area: float, geo_transform=None) -> list:
    """findContours → 面积过滤 → Shapely 抽稀 → 像素转经纬度（geo_transform）→
       [{geojson, area_px, area_m2(Geod), centroid, bbox}]"""
    ...

# ---- routers/compute.py ----
from fastapi import APIRouter
router = APIRouter()

@router.post("/compute/diff")
def diff(req): ...                    # TODO → compute_diff(...)，支持懒调用（无状态）

@router.post("/compute/vectorize")
def vectorize_api(req): ...           # TODO → vectorize(...)，支持懒调用（V1.2 约定）

@router.get("/health")
def health(): ...                     # TODO → {"status": "ok", "cpu_cores": N}
```

### 2.5 nlp-service 骨架

```python
# src/nlp_service/
# ├── main.py              # FastAPI 入口（cv-common 中间件/异常）
# ├── config.py            # L1 配置：LLM/地理编码完整 URL、key、provider（V2.3）
# ├── routers/
# │   └── nlp.py           # /nlp/parse、/health
# └── nlp/
#     ├── parser.py        # NLU 编排（FR-9.1），含逐请求降级与熔断（FR-9.7，V2.2）
#     ├── llm_client.py    # OpenAI 兼容协议客户端（httpx 直调完整 URL）+ instructor 结构化输出
#     ├── rule_based.py    # 规则模板降级（jieba + 句式模板 + 地名词典，FR-9.6）
#     ├── geocoder.py      # 地理编码（高德/百度/Nominatim，配置切换，FR-9.2）
#     ├── gazetteer.py     # 地名词典（降级版用）
#     └── circuit.py       # 进程内熔断器（V2.2 新增，FR-9.7）

# ---- nlp/parser.py ----
def parse(text: str, element_catalog: list[dict]) -> "NlParseResponse":
    """① NLU 抽取（逐请求降级，FR-9.7）：
         熔断器闭合 → 跳过 LLM 直接用规则；
         否则 try extract_by_llm（超时/5xx/连接失败/schema 校验重试耗尽）
           → 当次落 extract_by_rules，响应 nlu_provider=rule-based，
             记 structlog WARN（event=nlu_degraded, reason=timeout|error|schema_invalid）
       ② 要素映射（别名/修饰语归一；无法映射 → unsupported + 可识别类别，FR-9.3）
       ③ 地理编码（FR-9.8 部分成功：失败不报错，location=null +
         uncertain_fields 含 location，交用户在确认环节手选范围；
         仅当意图与位置均缺失、结果完全不可执行 → LOCATION_UNRESOLVED）
       ④ confidence < 0.7 字段标注 uncertain_fields（FR-9.4）"""
    ...

# ---- nlp/circuit.py（V2.2 新增）----
class CircuitBreaker:
    """进程内熔断器：60s 窗口内连续失败 N 次（默认 5）→ 断流 open_duration（默认 30s）
       → 半开试探一次，成功则闭合；参数经 L1 配置注入"""
    def allow(self) -> bool: ...
    def on_success(self) -> None: ...
    def on_failure(self) -> None: ...
    def state(self) -> str: ...        # closed / open / half_open，供 /health 上报

# ---- nlp/llm_client.py ----
def extract_by_llm(text: str, element_catalog: list[dict]) -> dict:
    """httpx 直接 POST 配置的完整端点 llm_url（请求体遵循 OpenAI chat completions 协议），
       不经 SDK 路径拼接（URL 完整性约束，需求约束 #10）；
       prompt 注入要素目录与意图枚举，instructor 约束结构化输出
       {intent, location_text, elements, periods, confidence}"""
    ...

# ---- nlp/rule_based.py ----
def extract_by_rules(text: str, gazetteer: dict) -> dict:
    """jieba 分词 + 固定句式模板（"识别/对比 + 地名 + 要素[+年份]"），nlu_provider=rule-based"""
    ...

# ---- nlp/geocoder.py ----
def geocode(location_text: str) -> dict:
    """→ {bbox, confidence, candidates}；provider 由配置切换（amap/baidu/nominatim）"""
    ...

# ---- routers/nlp.py ----
from fastapi import APIRouter
router = APIRouter()

@router.post("/nlp/parse")
def nlp_parse(req): ...               # TODO → parse(req.text, req.element_catalog)

@router.get("/health")
def health(): ...                     # TODO → nlu / geocoder 可用性上报
```

## 3. 核心模块详细设计

> 以下模块设计沿用 V1.3 内容，括号内标注 V2.0 归属服务。

### 3.1 推理引擎（infer-service）——对应 FR-5.1/5.2/3.2

#### 3.1.1 统一抽象（FR-5.1）

`InferenceEngine` 协议（骨架见 §2.3）：两类模型（语义分割、变化检测）统一抽象，analysis 编排层只依赖该接口。

#### 3.1.2 LocalOnnxEngine（FR-3.2）

- 初始化时按目标 provider 选择执行提供方与会话：
- `local-gpu`：`providers=['CUDAExecutionProvider']`，加载 **FP32** 模型；
- `local-cpu`：`providers=['CPUExecutionProvider']`，加载 **INT8 量化**模型，`intra_op_num_threads` 按 CPU 核数配置。
- 加载失败（如 GPU 版在无卡环境启动）→ 自动回退 CPU 会话，并在 `/health` 与每次响应的 `actual_provider` 中如实标记。
- 会话常驻内存（模型预热），推理时仅做 preprocess → run → 后处理。
- **多版本模型加载（FR-3.5 切换链路落地，评审 P-02）**：模型文件按 `{models.dir}/{name}/{version}/{fp32|int8}.onnx` 组织；请求体可携带 `model_name`/`model_version`（缺省用 L1 配置的默认版本），引擎按名+版本懒加载并缓存会话——Java 侧"激活新版本"=改 L2 配置热生效后随请求下发，infer-service 无需重启、无需被显式通知；模型文件经对象存储 + 命名卷下发（Java 设计 §3.7）。

#### 3.1.3 RemoteEngine

- httpx 客户端连接通用推理服务，端点/模型名/版本由配置注入（Java 侧 FR-3.4 切换版本时通过配置下发）；**端点为完整 URL，直接 POST，不拼接路径**（V2.3，需求约束 #10）。
- 支持 Tile 批量提交（FR-1.9）：按联调确定的 batch_size 分组调用；批量失败自动降级为逐块重试一次。
- 超时/重试由 Java 侧 Resilience4j 负责外层熔断，本服务只做单次调用超时控制（`timeout=25s`，略小于 Java 侧 30s，保证错误先在本层成形）。

#### 3.1.4 registry 引擎选择

```python
def get_engine(provider_hint: str | None) -> InferenceEngine:
    provider = provider_hint or settings.default_provider
    if provider == "remote":
        return remote_engine            # Java 已判定连通，本服务不再重复探测
    if provider == "local-gpu" and capability.gpu_usable():
        return local_gpu_engine
    return local_cpu_engine             # 兜底，响应中标记 actual_provider
```

### 3.2 影像处理管线（infer-service，编解码工具在 cv-common）

#### 3.2.1 loader

- 支持 PNG/JPEG（OpenCV 解码）与 TIFF/GeoTIFF（rasterio 解码，同时提取地理参考 transform 供面积换算，FR-6.5）。
- 影像统一转为 RGB uint8 数组；记录 `geo_transform`——无内嵌 transform 时按请求 `geo_extent` + 图像宽高经 `cv_common.geo.extent_to_geo_transform` 线性推导（与 tile_range_to_extent 同源，评审 D-01）；GeoTIFF 内嵌 transform 时以 `geo_extent` 交叉校验（偏差超容差 → GEO_EXTENT_MISMATCH）。
- **坐标系约定（V1.1）**：系统坐标统一为 CGCS2000 经纬度（度）。`geo_transform` 换算出的坐标单位为度；GeoTIFF 自带 CRS 时校验其为地理坐标系（EPSG:4490/4326 系列），投影坐标系的 GeoTIFF 拒绝并返回 `INVALID_INPUT`；瓦片拼接图无内嵌 CRS，以 Java 侧校验过的 `geo_extent` 为准（随请求携带，§4.1/§4.2，评审 D-01）。

#### 3.2.2 tiler（FR-1.3/6.7）

- 切分：默认 256×256，**重叠 32 像素**（stride=224），保证拼接处类别连续。
- 拼接融合：重叠区取两 Tile 概率图的均值后再 argmax，避免接缝（FR-6.7）。
- 大图内存控制：流式处理——切一块、推一块、写回结果画布一块，不在内存同时持有全部 Tile。

#### 3.2.3 postprocess（FR-1.6/7.4）

```python
# 形态学去噪 + 小区域过滤
kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)    # 去噪点
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)   # 连碎片
mask = remove_small_regions(mask, min_area)              # 连通域过滤（FR-1.6/7.4）
```

#### 3.2.4 colorize（FR-6.3/7.3）

- 要素合成蒙版：RGBA 画布，按请求中的类别颜色表（Java 从 element_catalog 下发）着色，非覆盖区域 alpha=0；同时产出各类别独立二值蒙版。
- 差异蒙版：added→配置色（默认绿）、removed→配置色（默认红）、unchanged→透明（FR-7.3）。

#### 3.2.5 alignment（配准校验，FR-1.2/7.7，评审 D-03）

- **能力范围（本期）**：几何偏差**校验告警**——双图整体平移估计采用相位相关（`cv2.phaseCorrelate`，灰度化 + 降采样至长边 ≤1024 后计算，CPU 毫秒级）；自动配准（重采样对齐）列 M6（需求第 12 章）。
- **适用链路**：`/infer/change-detection`（双图同请求到达）编排内置、推理前执行；双期比对/多期时序链路两期影像**分请求**到达本服务，像素级平移估计需引入配对机制，本期由 Java 侧 geo_extent 一致性校验（FR-8.7）兜底范围级偏差，像素级校验随 M6 自动配准一并落地。
- **判定**：估计平移量（两轴取大）超 `alignment.max_shift_px`（L1 配置，默认 4px）→ 抛 `AlignmentFailedError`（ALIGNMENT_FAILED / 422），message 注明“自动配准能力本期未启用（M6 交付）”。
- **auto_align 语义（本期）**：参数透传并记录，不改变处理流程；响应携带 `estimated_shift_px`（实测平移量）与 `auto_aligned=false`；超阈值时**不静默忽略**（避免用户误以为已纠偏），统一返回 ALIGNMENT_FAILED。

### 3.3 分析编排

#### 3.3.1 要素识别（infer-service，FR-6）

```
run_segmentation(req)
  ① loader 读图 → ② tiler 切分 → ③ engine.segment 逐批推理 → ④ 融合拼接
  ⑤ 按 class_mapping 提取选定类别的二值蒙版（elements 超模型类别 → UNSUPPORTED_ELEMENT，FR-6.6）
  ⑥ postprocess → ⑦ colorize（合成 + 独立蒙版）
  ⑧ 统计：各类 area_px / ratio / patch_count（FR-6.5）；
     有 geo_transform 时按 CGCS2000 椭球大地测量换算 area_m2（cv_common.geo.geodesic_area）
  返回：{combined_mask_png, per_class_masks{element: png}, statistics, model_info, actual_provider}
```

#### 3.3.2 端到端变化检测（infer-service，FR-1）

与分割同管线，输入双时相影像；loader 读双图后先经 alignment 配准校验（§3.2.5，超阈值返 ALIGNMENT_FAILED）；engine.detect_change 输出变化概率图 → 按 threshold 二值化（FR-1.5 同时返回概率图供前端动态调阈）→ 后处理 → 变化蒙版 + 概率图返回。

#### 3.3.3 差异计算（compute-service，FR-7.2/7.5）

纯像素计算，不调模型：

```python
added    = after_mask & ~before_mask     # 新增：仅新图有
removed  = before_mask & ~after_mask     # 减少：仅旧图有
# 后处理（FR-7.4）→ 统计（FR-7.5）：
# added_px / removed_px / net_change_px / change_rate = (after-before)/before
```

输入为两期二值蒙版（Java 编排：两期分割 mask 数组在同一请求中给出）。边缘伪差异抑制（FR-7.7）：对蒙版边界 1~2 像素做腐蚀后再比对，配准误差导致的边缘噪声被过滤。

#### 3.3.4 矢量化（compute-service，FR-1.7/6.4/7.6）

```python
from cv_common.geo import GEOD, geodesic_area

contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
for c in contours:
    if cv2.contourArea(c) < min_area: continue
    poly_px = shapely.simplify(Polygon(c[:, 0, :]), tolerance=1.0)   # 像素坐标抽稀
    poly_geo = transform(poly_px, geo_transform) if geo_transform else poly_px  # 像素→经纬度
    yield {
        "geojson": mapping(poly_geo),            # GeoJSON 几何（CGCS2000 经纬度）
        "area_px": poly_px.area,
        "area_m2": geodesic_area(poly_geo) if geo_transform else None,
        "centroid": [poly_geo.centroid.x, poly_geo.centroid.y],
        "bbox": list(poly_geo.bounds),           # minx,miny,maxx,maxy（Java 冗余列数据源）
    }
```

差异任务新增/减少分别矢量化（FR-7.6），region_type 由 Java 按图层类型映射。懒调用约定（V1.2）：本接口独立可调用、与任务管线解耦，任务执行期跳过、导出时由 result-service 现调。

### 3.4 NLP 模块（nlp-service）——对应 FR-9（P2）

#### 3.4.1 编排流程（V2.2 重写：逐请求降级 + 熔断 + 部分成功）

```
/nlp/parse(text, element_catalog)
  ① NLU 抽取（逐请求降级，FR-9.7）：
     熔断器闭合（circuit.state() != closed）→ 直接用 rule_based
     否则 try llm_client：
       prompt 注入当前要素目录与意图枚举，
       instructor 约束结构化输出 {intent, location_text, elements, periods, confidence}，
       schema 校验失败重试 2 次
       发生 超时(15s 可配)/5xx/连接失败/重试耗尽 → 当次落 rule_based：
         jieba 分词 + 固定句式模板 + 地名词典（仅支持"识别/对比 + 地名 + 要素[+年份]"句式）
         响应标记 nlu_provider=rule-based（FR-9.6）
         记 structlog WARN：event=nlu_degraded, reason=timeout|error|schema_invalid
       失败计数进熔断器：60s 窗口连续 5 次 → 断流 30s → 半开试探（参数可配）
  ② 要素映射：elements 对照 element_catalog（含别名/修饰语归一，如"建筑群"→building）；
     无法映射 → unsupported 列表 + 当前可识别类别（FR-9.3）
  ③ 地理编码（FR-9.8 部分成功降级）：
     location_text → geocoder → bbox + confidence；多候选 → candidates 列表（FR-9.2）
     编码失败 → 不报错：location=null + uncertain_fields 含 "location" + need_confirm=true，
                前端在确认环节提供手动选范围入口
     仅当 意图缺失 且 位置缺失（结果完全不可执行）→ LOCATION_UNRESOLVED 错误
  ④ 组装响应：confidence < 0.7 的字段在 uncertain_fields 中标注（FR-9.4 前端强制确认高亮）
```

#### 3.4.2 外部依赖适配

| 依赖 | 公网可达 | 内网离线 |
| --- | --- | --- |
| LLM | 通用推理服务平台上的 LLM（OpenAI 兼容协议） | 自部署开源模型（同协议接入） |
| 地理编码 | 高德/百度 API | 自部署 Nominatim + 行政区划数据 |

两类依赖均通过配置**完整 URL** 切换（V2.3，需求约束 #10：值即"可直接发起请求的完整地址"，代码不拼接任何固定路径；地理编码仅按各 provider 协议拼接 query 参数，不假设路径结构），代码零改动；**LLM 故障由逐请求降级 + 熔断吸收（§3.4.1），地理编码故障降级为部分成功**；仅当 NLU 与地理编码均不可用（部署期即未配置）时 `/nlp/parse` 返回 `NLU_UNAVAILABLE`，Java 侧按可裁剪模块处理（需求 6.9 评估结论）。

### 3.5 健康与能力上报——对应 FR-5.5/5.7

各服务 `GET /health`（Java 的三级解析与监控依赖 infer-service 的此接口）：

**infer-service /health**：

```json
{
  "status": "ok",
  "gpu_available": true,
  "gpu_usable": true,
  "vram_mb": 15360,
  "cpu_cores": 8,
  "models": {
    "segmentation":  {"loaded": true, "version": "v2.0", "provider": "local-gpu", "class_ids": [0,1,2,3,4]},
    "change_detect": {"loaded": true, "version": "v1.2", "provider": "local-gpu"}
  },
  "remote_configured": false
}
```

**compute-service /health**：`{"status": "ok", "cpu_cores": 8}`（轻量，无模型）。

**nlp-service /health**：`{"status": "ok", "nlu": {"provider": "llm", "available": true, "circuit": "closed"}, "geocoder": {"provider": "nominatim", "available": true}}`（V2.2：`nlu.circuit` 上报熔断器状态 closed/half_open/open，供 Java 侧监控与告警）。

探测实现：CUDA 可用性用 `onnxruntime.get_available_providers()` + 试建小会话验证；显存用 pynvml（GPU 版镜像内置）。

## 4. 接口设计（请求/响应 Schema 摘要）

> Schema 的 Pydantic 定义全部在 cv-common，三服务与 Java 侧 OpenAPI diff 共用此事实源。接口字段与 V1.3 完全一致（微服务拆分不改变契约）。

### 4.1 infer-service：POST /infer/segmentation

```json
// 请求
{
  "image_url": "http://oss-internal/.../t1.png",   // Java 签发的内网预签名 URL（评审 P-03）
  "model_name": "landcover-seg", "model_version": "v2.0",   // 可选：缺省用默认版本（FR-3.5 切换链路，评审 P-02）
  "elements": ["forest", "building"],
  "class_mapping": {"forest": 1, "building": 3},
  "colors": {"forest": "#228B22", "building": "#CD853F"},
  "min_area": 100,
  "geo_extent": [104.01, 30.69, 104.07, 30.73]   // 必填：CGCS2000 经纬度范围（评审 D-01）；无内嵌 transform 的影像据此推导 geo_transform，GeoTIFF 场景用于交叉校验
}
// 响应
{
  "combined_mask_png_b64": "...",
  "per_class_masks": {"forest": "...b64...", "building": "...b64..."},
  "statistics": {"forest": {"area_px": 152300, "area_m2": 380750.5, "ratio": 0.36, "patch_count": 12}},
  "geo_transform": [104.01, 2.5e-6, 0, 30.73, 0, -2.5e-6],
  "model_info": {"name": "landcover-seg", "version": "v2.0"},
  "actual_provider": "local-cpu",
  "elapsed_ms": 2100
}
```

### 4.2 infer-service：POST /infer/change-detection

请求：`{before_url, after_url, geo_extent, threshold, min_area, auto_align}`（before/after 均为 Java 签发的预签名 URL；`geo_extent` 必填、语义同 §4.1（评审 D-01）；`auto_align` 可选透传，本期语义见 §3.2.5（评审 D-03）；可选 model_name/model_version 同 §4.1）；响应：`{mask_png_b64, probmap_png_b64, statistics, estimated_shift_px, auto_aligned, model_info, actual_provider}`。

### 4.3 compute-service：POST /compute/diff

```json
// 请求（蒙版以 base64 二值 PNG 传入，无需重新推理）
{"before_mask_b64": "...", "after_mask_b64": "...", "element": "forest",
 "min_area": 100, "colors": {"added": "#00FF00", "removed": "#FF0000"},
 "geo_transform": [...]}
// 响应
{"diff_mask_png_b64": "...", "statistics": {"added_px": 4500, "removed_px": 17000,
 "net_change_px": -12500, "change_rate": -0.082, "added_m2": 11250.0, "removed_m2": 42500.0}}
```

### 4.4 compute-service：POST /compute/vectorize

请求：`{mask_b64, min_area, geo_transform}`；响应：`{regions: [{geojson, area_px, area_m2, centroid, bbox}]}`。

### 4.5 nlp-service：POST /nlp/parse

请求：`{text, element_catalog}`——`element_catalog` **必填**，由 Java 从 config-service 要素目录取出随请求下发（含 id/name/model_class_id/enabled，评审 D-02）。响应：`{intent, location{raw,bbox,confidence,candidates}（可空）, elements, periods, confidence, need_confirm, unsupported, nlu_provider, uncertain_fields（可空，FR-9.4/9.8 低置信度与失败字段标注）}`，字段语义同需求 7.4。**要素别名/修饰语词表（“建筑群”→building、“雪山”→snow）由 nlp-service 内置维护**（与句式模板、地名词典同属 NLP 归一逻辑，评审 D-02），不经 Java 下发、不入配置。

### 4.6 错误约定

| HTTP | code | 场景 | 产生服务 |
| --- | --- | --- | --- |
| 400 | UNSUPPORTED_ELEMENT | 类别超出模型输出（FR-6.6） | infer |
| 400 | IMAGE_DECODE_FAILED | 影像拉取/解码失败 | infer |
| 400 | GEO_EXTENT_MISMATCH | 双期影像尺寸/地理范围不一致（FR-8.7 辅助；V2.5 更名并与对外码一致，评审 D-05） | infer / compute |
| 422 | ALIGNMENT_FAILED | 双期影像配准偏差超阈值（FR-1.2/7.7，评审 D-03） | infer |
| 400 | INPUT_TOO_LARGE | local-cpu 模式输入超限 | infer |
| 500 | INFERENCE_FAILED | 推理执行异常 | infer |
| 503 | MODEL_NOT_READY | 模型未加载 | infer |
| 503 | NLU_UNAVAILABLE | NLU/地理编码不可用（FR-9 裁剪场景） | nlp |
| 422 | LOCATION_UNRESOLVED | 意图与位置均缺失、解析完全不可执行（FR-9.8，评审 D-04） | nlp |

Java 侧统一 `PythonErrorDecoder`（common 模块）将上述 code 映射为对外错误码（Java 详细设计 §6.2）。

## 5. 性能与资源设计（infer-service 为主，对应需求 8.1）

| 措施 | 说明 |
| --- | --- |
| 推理信号量 | 进程内 `threading.Semaphore(N)`（CPU 默认 2 / GPU 默认 4），超并发请求排队，防内存/显存打爆；**实现口径（评审 D-06）**：推理路由为同步 `def`、由 FastAPI 线程池执行，故用线程信号量而非 asyncio.Semaphore（避免同步工作线程跨事件循环 acquire） |
| 流式 Tile | 大图不同时持有全部 Tile，结果画布预分配后逐块写回 |
| 数组复用 | preprocess 缓冲池复用 numpy 数组，减少 GC 压力 |
| CPU 线程 | `intra_op_num_threads = cpu_cores // 信号量数`，避免多路推理争抢 |
| 输入规模限制 | local-cpu 模式请求尺寸 > 2048 时拒绝（`INPUT_TOO_LARGE`），由 Java 侧提前拦截为主、本服务兜底 |
| 多实例扩展 | 三服务均无状态，infer-service 可按 GPU 卡数横向扩；Java 侧直连地址可配多个（轮询） |
| 目标指标 | 单 Tile CPU（INT8）≤ 3s；1024×1024 要素识别（16 Tile）CPU ≤ 60s；GPU 路径按需求 8.1 远程模式指标 |

## 6. 配置项清单（pydantic-settings，环境变量注入）

**配置责任划分（V1.3 明确，V2.0 不变，对应需求 FR-10）**：三服务只持有 **L1 部署配置**（端口、模型路径、并发槽位、外部依赖地址），重启生效、环境变量注入。**所有业务策略参数不落在 Python**——threshold、min_area、类别映射、配色、diff 分色等均由 Java 运行配置（app_config）持有，逐请求随参数下发（见第 4 章各接口请求体）。管理员在线改配置只动 Java config-service 一处，Python 无需重启。可用能力经各服务 `/health` 上报，由 Java 汇聚进 `/api/v1/capabilities`。

**URL 完整性（V2.3，需求约束 #10）**：下列所有 `*_url` / `endpoint` 配置项的值均为**可直接发起请求的完整 URL**（协议 + 主机 + 端口 + 完整路径，允许携带任意网关/反代前缀），代码不得在其后拼接固定路径；启动时校验各 URL 可解析为绝对地址（scheme 为 http/https），非法即拒绝启动并明确报错。

```yaml
# ===== 三服务公共（V2.1 新增，FR-10.7）=====
# security.auth_enabled 与 Java 侧 security.auth.enabled 为同一总开关，
# 由同一环境变量 AUTH_ENABLED 注入，保证两侧语义一致
security:
  auth_enabled: ${AUTH_ENABLED:-true}   # false 时跳过 X-Internal-Token 校验，任何内网调用方可用
  internal_token: ${INTERNAL_TOKEN}     # 与 Java 共享的内部密钥

# infer-service（端口 8001）
service: { port: 8001, default_provider: local-cpu }
models:
  dir: /models
  segmentation:
    fp32: fp32.onnx               # 文件名固定约定 {name}/{version}/{fp32|int8}.onnx（§3.1.2，评审 P-02）
    int8: int8.onnx
    name: landcover-seg
    version: v2.0
    class_ids: [0, 1, 2, 3, 4]      # 0=背景
  change_detection:
    fp32: fp32.onnx
    int8: int8.onnx
    name: change-detection
    version: v1.2
tiling: { tile_size: 256, overlap: 32 }
concurrency: { cpu_slots: 2, gpu_slots: 4 }
remote: { endpoint: "", timeout_s: 25, batch_size: 8 }   # endpoint 为完整推理端点 URL；仅 provider=remote 时使用
limits: { local_cpu_max_input: 2048 }
alignment: { max_shift_px: 4 }        # 配准校验阈值（§3.2.5，评审 D-03）
storage_reader: { timeout_s: 30 }

# compute-service（端口 8002）：仅端口与日志级别，无模型
service: { port: 8002 }

# nlp-service（端口 8003）
service: { port: 8003 }
nlp:
  llm_url: ""                        # 完整 chat completions 端点（如 http://llm.internal/ai/gw/v1/chat/completions）；空 = LLM 不可用 → 常驻规则解析
  llm_model: qwen2.5-14b-instruct
  llm_timeout_s: 15                  # V2.2：单次 LLM 调用超时，超时即当次降级（FR-9.7）
  circuit:                           # V2.2：进程内熔断器参数（FR-9.7）
    failure_threshold: 5             # 窗口内连续失败次数触发断流
    window_s: 60
    open_duration_s: 30              # 断流时长，之后半开试探
  geocoder_provider: nominatim       # amap / baidu / nominatim
  geocoder_url: ""                   # 完整地理编码端点 URL（查询参数按 provider 协议拼接）
  geocoder_key: ""
```

## 7. 部署设计（V2.0 重写：按服务独立镜像）

### 7.1 镜像策略

| 镜像 | 基础镜像 | 关键差异 |
| --- | --- | --- |
| `map-change-infer:cpu` | python:3.11-slim | onnxruntime（CPU 版）+ 全量依赖，内网离线 pip 源构建 |
| `map-change-infer:gpu` | nvidia/cuda:12.x-runtime | onnxruntime-gpu + pynvml；挂 NVIDIA Container Toolkit 运行 |
| `map-change-compute` | python:3.11-slim | 无 onnxruntime（纯 OpenCV/Shapely/pyproj），镜像最小 |
| `map-change-nlp` | python:3.11-slim | jieba + httpx + instructor，镜像最小 |

infer-service 同一代码库双镜像，依赖差异由 `requirements.txt` / `requirements-gpu.txt` 区分；GDAL（rasterio 依赖）在镜像内编译安装并缓存层。compute/nlp 不装 rasterio/onnxruntime，保持轻量与快速交付。

### 7.2 运行约束

- 三服务仅绑定内网网卡，与 Java 微服务群容器网络互通；不暴露公网端口；不接入 Nacos（Java 侧配置地址直连，需求约束 #9）。
- 内部鉴权（V2.1）：`security.auth_enabled=true` 时各服务经 cv-common `auth_middleware` 校验 `X-Internal-Token`（/health 豁免）；`false` 时直接放行。开关必须与 Java 侧保持一致（同一 `AUTH_ENABLED` 环境变量注入），否则会出现 Java 已鉴权但 Python 拒绝（或反之）的不一致状态。
- 模型目录以 volume 挂载到 infer-service，模型更新 = 替换文件 + 重启（热加载为 FR-3.5 P2，后续实现 `/admin/reload`）。
- 日志输出 stdout（structlog JSON，cv-common 统一格式，含 service 字段），由 Loki 采集；`X-Task-Id` 全链路透传。

## 8. 测试策略

| 层 | 内容 | 工具 |
| --- | --- | --- |
| 单元测试 | tiler 融合无缝性、differ 三态划分正确性、vectorizer bbox/面积精度、colorize 颜色映射、规则 NLU 句式、cv-common 坐标公式 | pytest + 小尺寸合成影像 fixtures |
| 引擎测试 | LocalOnnxEngine 用伪 ONNX 模型（恒等输出）验证管线；RemoteEngine 用 respx 打桩 | pytest + respx |
| 契约测试 | 三服务各导出 OpenAPI 快照，与 Java 侧 CI diff | schemathesis（可选） |
| 性能基准 | 单 Tile / 1024 图 CPU 耗时基线，回归防退化 | pytest-benchmark |
| 骨架验收（M1 特有） | cv-common 可安装；三服务 uvicorn 可启动；全部路由返回 501/占位响应且符合 schema；三份 OpenAPI 快照生成 | — |
| 验收对照 | 需求第 13 章第 1、2、4 条涉及本服务群的部分 | — |

## 9. 交付物清单（仅 M1 里程碑：工程脚手架）

> 本章仅列 **M1（脚手架里程碑）** 的交付物，非系统全部交付范围。M1 目标：monorepo 脚手架全部就位——cv-common 包 + 三服务目录、schema 定义、类/函数签名完整，实现留空（`...` 或 `raise NotImplementedError`），服务可启动、契约可导出。后续里程碑的实现交付见《Python 服务开发任务清单 V2.1》M2~M5。

1. monorepo 工程：cv-common 可编辑安装包（schema/geo/imaging/errors/logging 骨架）+ 三服务 FastAPI 骨架（路由、引擎协议、函数签名同本文第 2 章）。
2. 三服务 `/health` 返回结构符合 §3.5（字段可占位）。
3. 三服务 Dockerfile（infer 含 cpu/gpu 双版）+ docker-compose.yml。
4. 三份 OpenAPI 快照纳入 docs/openapi/（与 Java 侧契约对齐）。
5. 伪 ONNX 模型制作脚本占位（M2 实现，用于无真实模型时联调）。
