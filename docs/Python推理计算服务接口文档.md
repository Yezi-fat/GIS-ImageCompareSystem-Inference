# Python 推理计算服务接口文档

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.1 |
| 编写日期 | 2026-09-12 |
| 适用范围 | Python 推理计算服务群（infer-service / compute-service / nlp-service），**仅内网**，供 Java 业务服务群（analysis-service / result-service）调用 |
| 依据 | 详细设计文档 V2.6、需求分析文档 V2.8、docs/openapi/ 快照；本文全部示例经运行中服务实测（伪模型 v0.0-fake 链路） |
| V1.1 修订 | §1.4 新增 `GET /infer/models` 模型发现接口（bug-2026-09-28 Q2/Q3）；§0.2 `X-Inference-Provider` 非法取值行为明确为 400 INVALID_INPUT（原静默兜底） |

## 0. 通用约定

### 0.1 服务清单

| 服务 | 默认端口 | 职责 | 接口前缀 |
| --- | --- | --- | --- |
| infer-service | 8001 | 推理（语义分割 / 端到端变化检测）、Tile 管线、模型管理、环境能力上报 | `/infer/*` |
| compute-service | 8002 | 计算（差异三态、矢量化、Geod 面积、蒙版分色），纯 CPU 无状态 | `/compute/*` |
| nlp-service | 8003 | 自然语言任务解析（NLU + 地理编码，可整体裁剪） | `/nlp/*` |

三服务均无状态、不接入注册中心，Java 侧以配置地址直连（约束 #9）。

### 0.2 请求头

| 请求头 | 必选 | 说明 |
| --- | --- | --- |
| `X-Internal-Token` | 鉴权开启时 | 与 Java 共享密钥（同一 `INTERNAL_TOKEN` 环境变量注入）；`AUTH_ENABLED=false` 时免带；`/health` 恒豁免（FR-10.7） |
| `X-Inference-Provider` | 仅 `/infer/*` 可选 | 推理提供方提示：`remote` / `local-gpu` / `local-cpu`（小写连字符）；缺省时按 infer-service 本地默认配置兜底；实际执行不一致时响应 `actual_provider` 如实标记。**非法取值（如 `local_gpu` 下划线形式）返回 400 INVALID_INPUT，不静默兜底**（V1.1，bug-2026-09-28 Q1） |
| `X-Task-Id` / `X-Trace-Id` | 建议 | 透传进结构化日志（全链路追踪，需求 8.4） |

### 0.3 数据约定

- **影像输入**：Java 签发的对象存储**内网预签名 URL**（`image_url` 等，评审 P-03）——Python 零配置零令牌直取；URL 须在任务执行期签发或 TTL 覆盖排队+执行（J-02）；过期/失效返回 `IMAGE_DECODE_FAILED`（message 携带存储端 HTTP 状态，可重签重试）。
- **蒙版/概率图**：一律 **PNG base64** 字符串随响应返回（`compute/*` 的输入蒙版同格式上行）；合成/差异蒙版为 RGBA（非覆盖区域全透明），独立蒙版为单通道 0/255。
- **geo_transform**：GDAL 六参数仿射 `[x0, px_w, 0, y0, 0, -px_h]`（度/像素，CGCS2000 经纬度）；infer 响应返回，Java 透传给 compute 的 `diff`/`vectorize`。
- **坐标系**：CGCS2000 经纬度（度）；面积/中心点为椭球大地测量（pyproj Geod）。
- **错误响应**：`{"code", "message", "trace_id"}`；错误码见 §4。

---

## 1. infer-service（:8001）

### 1.1 要素识别（语义分割）

| 项目 | 内容 |
| --- | --- |
| 方法与路径 | `POST /infer/segmentation` |
| 对应需求 | FR-6（要素范围识别）、FR-6.6（类别超范围明确报错） |
| 内容类型 | application/json |

**功能说明**：对单期影像执行语义分割，识别指定要素类别（可多选）的空间覆盖范围。管线：预签名 URL 拉图（GeoTIFF 内嵌 CRS 校验并与 `geo_extent` 交叉校验，PNG/JPEG 按 `geo_extent` 线性推导 geo_transform）→ 256×256 重叠 32px 流式 Tile 推理 → 重叠区概率均值融合（无接缝）→ 类别提取 → 形态学去噪 + 小区域过滤 → 合成蒙版着色 + 各类独立蒙版 → 双单位面积统计。

**请求参数**：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| image_url | string | 是 | 影像预签名 URL（PNG/JPEG/TIFF/GeoTIFF） |
| geo_extent | float[4] | 是 | `[minx,miny,maxx,maxy]` CGCS2000 经纬度；GeoTIFF 场景用于交叉校验，不一致返 GEO_EXTENT_MISMATCH |
| elements | string[] | 是 | 要素类别 ID 列表（如 `["forest","building"]`） |
| class_mapping | object | 是 | 要素类别 ID → 模型输出类别 ID（Java 自 element_catalog 下发） |
| colors | object | 是 | 要素类别 ID → `#RRGGBB` 叠加颜色 |
| min_area | int | 否 | 最小斑块面积（像素，默认 100） |
| model_name / model_version | string | 否 | 缺省用 L1 配置默认版本（FR-3.5 切换链路） |

**响应参数**（200）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| combined_mask_png_b64 | string | 按类别着色的合成蒙版 PNG（RGBA）base64 |
| per_class_masks | object | 各类别独立二值蒙版 base64（键为要素 ID） |
| statistics | object | 按类别键控：`{area_px, area_m2, ratio, patch_count}`（area_m2 为 CGCS2000 椭球面积） |
| geo_transform | float[6] | GDAL 六参数仿射（Java 透传 compute 侧） |
| model_info | object | `{name, version, provider, class_ids}`（FR-3.3 追溯） |
| actual_provider | string | 实际执行提供方（降级时如实标记，如 local-gpu 提示回退 local-cpu） |
| elapsed_ms | int | 端到端耗时（毫秒） |

**请求示例**：

```bash
curl -X POST http://infer-service:8001/infer/segmentation \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H "X-Task-Id: $TASK_ID" \
  -H 'Content-Type: application/json' -d '{
    "image_url": "http://storage-service:8004/files/.../scene.png?sign=...",
    "elements": ["forest", "building"],
    "class_mapping": {"forest": 1, "building": 4},
    "colors": {"forest": "#228B22", "building": "#CD853F"},
    "min_area": 100,
    "geo_extent": [104.01, 30.69, 104.07, 30.73]
  }'
```

**实测响应**（512×512 合成影像，伪模型，2026-09-12 实测）：

```json
{
  "combined_mask_png_b64": "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAAVAElEQVR4Ae3BQQEC...（base64 截断展示）",
  "per_class_masks": {
    "forest": "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAAAAADRE4smAAAG1UlEQVR4AeXBAQEA...（截断）",
    "building": "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAAAAADRE4smAAAFF0lEQVR4Ae3BQQEA...（截断）"
  },
  "statistics": {
    "forest":   {"area_px": 131072, "area_m2": 12743925.17, "ratio": 0.5,    "patch_count": 1},
    "building": {"area_px": 16382,  "area_m2": 1592553.38,  "ratio": 0.0625, "patch_count": 1}
  },
  "geo_transform": [104.01, 0.0001171875, 0.0, 30.73, 0.0, -0.000078125],
  "model_info": {"name": "landcover-seg", "version": "v0.0-fake", "provider": "local-cpu", "class_ids": [0, 1, 2, 3, 4]},
  "actual_provider": "local-cpu",
  "elapsed_ms": 72
}
```

**典型错误**：`UNSUPPORTED_ELEMENT`（类别超映射或超模型类别表，FR-6.6）、`INPUT_TOO_LARGE`（local-cpu 模式 >2048px，R-04）、`IMAGE_DECODE_FAILED`（URL 过期/解码失败）、`GEO_EXTENT_MISMATCH`（GeoTIFF 内嵌范围与请求不一致）、`MODEL_NOT_READY`（模型文件缺失）。

```json
{"code": "UNSUPPORTED_ELEMENT", "message": "要素类别 ['water'] 不在类别映射中（可识别：['forest']）", "trace_id": "7438c45b43bf43d2939b01ecf561c45a"}
```

### 1.2 端到端变化检测

| 项目 | 内容 |
| --- | --- |
| 方法与路径 | `POST /infer/change-detection` |
| 对应需求 | FR-1（端到端变化检测）、FR-1.2/7.7（配准校验告警路径，评审 D-03）、FR-1.5（概率图） |
| 内容类型 | application/json |

**功能说明**：变化检测模型直接对两期影像输出变化蒙版。双图同请求到达，**推理前内置配准校验**（§3.2.5：相位相关整体平移估计，默认阈值 4px 可配 `ALIGNMENT__MAX_SHIFT_PX`）——超阈值返回 `ALIGNMENT_FAILED`（422），本期不做自动配准（M6）；阈值内正常出结果并在响应携带实测平移量。

**请求参数**：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| before_url / after_url | string | 是 | 前后两期影像预签名 URL（须同尺寸，否则 GEO_EXTENT_MISMATCH） |
| geo_extent | float[4] | 是 | 同 §1.1 |
| threshold | float | 否 | 变化概率二值化阈值（默认 0.5，FR-1.5） |
| min_area | int | 否 | 最小变化区域面积（像素，默认 100） |
| auto_align | bool | 否 | 自动配准开关：本期仅透传记录、不改流程（响应 `auto_aligned=false`），超偏差统一返 ALIGNMENT_FAILED 并注明 M6（不静默忽略） |
| model_name / model_version | string | 否 | 同 §1.1 |

**响应参数**（200）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| mask_png_b64 | string | 变化蒙版 PNG base64（与输入同尺寸，FR-1.4） |
| probmap_png_b64 | string | 变化概率图 PNG base64（前端可按阈值动态调整，FR-1.5） |
| statistics | object | `{change_count, total_area_px, total_area_m2}`（FR-1.8） |
| estimated_shift_px | float | 配准校验实测整体平移量（像素） |
| auto_aligned | bool | 本期恒为 false（自动配准列 M6） |
| geo_transform / model_info / actual_provider / elapsed_ms | — | 同 §1.1 |

**请求示例**：

```bash
curl -X POST http://infer-service:8001/infer/change-detection \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H 'Content-Type: application/json' -d '{
    "before_url": "http://storage-service:8004/files/.../before.png?sign=...",
    "after_url":  "http://storage-service:8004/files/.../after.png?sign=...",
    "geo_extent": [104.01, 30.69, 104.07, 30.73],
    "threshold": 0.5, "min_area": 100, "auto_align": false
  }'
```

**实测响应**（512×512 双期合成影像，变化块 200×200，伪模型实测）：

```json
{
  "mask_png_b64": "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAAAAADRE4smAAAGyklEQVR4Ae3BgQ3A...（截断）",
  "probmap_png_b64": "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAAAAADRE4smAAAgAElEQVR4AezB2ZJl...（截断）",
  "statistics": {"change_count": 1, "total_area_px": 39992, "total_area_m2": 3888495.37},
  "estimated_shift_px": 0.06,
  "auto_aligned": false,
  "geo_transform": [104.01, 0.0001171875, 0.0, 30.73, 0.0, -0.000078125],
  "model_info": {"name": "change-detection", "version": "v0.0-fake", "provider": "local-cpu", "class_ids": null},
  "actual_provider": "local-cpu",
  "elapsed_ms": 24
}
```

**典型错误**：`ALIGNMENT_FAILED`（422，平移超阈值；message 注明"自动配准能力本期未启用（M6 交付）"）、`GEO_EXTENT_MISMATCH`（双图尺寸不一致）、其余同 §1.1。

### 1.3 健康与能力上报

| 项目 | 内容 |
| --- | --- |
| 方法与路径 | `GET /health`（免鉴权） |
| 对应需求 | FR-5.5/5.7（Java 三级提供方解析与 capabilities 聚合的数据源，J-08） |

**功能说明**：上报推理环境探测（CUDA 可用性试建小会话验证、显存、CPU 核数）、两类模型就绪状态（ModelStore 会话缓存）、远程端点配置与否。`gpu_available`（provider 列表存在）与 `gpu_usable`（试建会话通过）区分上报。

**实测响应**：

```json
{
  "status": "ok",
  "gpu_available": false, "gpu_usable": false, "vram_mb": null, "cpu_cores": 32,
  "models": {
    "segmentation":  {"loaded": true, "version": "v0.0-fake", "provider": "local-cpu", "class_ids": [0, 1, 2, 3, 4]},
    "change_detect": {"loaded": true, "version": "v0.0-fake", "provider": "local-cpu", "class_ids": null}
  },
  "remote_configured": false
}
```

> V1.1：`models.segmentation.class_ids` 的来源——显式配置 `MODELS__SEGMENTATION__CLASS_IDS` 优先；
> 缺省时从模型 ONNX 元数据 names 自动推导（检测模型类别序号直用、末位通道为背景）；
> 模型未内嵌 names 时为 null。

### 1.4 模型发现（V1.1 新增）

| 项目 | 内容 |
| --- | --- |
| 方法与路径 | `GET /infer/models` |
| 对应事项 | bug-2026-09-28 Function-Q2（要素目录与模型类别核对）/ Function-Q3（Java 模型列表对账） |

**功能说明**：扫描模型目录（`{name}/{version}/{fp32,int8}.onnx`），返回推理服务实际可用模型清单——双产物齐全性、类别表（读 ONNX 元数据 names，索引=类别 ID）、概率图通道数、会话加载状态、是否当前默认版本。供 Java 模型管理页展示/对账、element_catalog 的 `model_class_id` 映射校验。只读接口，鉴权同其他接口（/health 豁免口径不含本接口）。

**响应参数**（200）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| models_dir | string | 模型目录（`MODELS__DIR` 实值） |
| models | array | 模型列表（按名聚合）：`{name, versions[]}` |
| models[].versions[].version | string | 版本号（目录名） |
| models[].versions[].fp32 / int8 | bool | 对应产物文件是否存在 |
| models[].versions[].class_labels | string[]\|null | 类别名称表（ONNX names，索引=类别 ID）；未内嵌为 null |
| models[].versions[].class_count | int\|null | 概率图通道数：检测模型（YOLO 系）= len(labels)+1（末位背景）；逐像素分割模型 = len(labels) |
| models[].versions[].loaded | bool | 会话是否已加载常驻 |
| models[].versions[].default_for | string[] | 作为默认版本的模型类型（`segmentation` / `change_detection`），非默认为空数组 |

**实测响应**（开发机 models/ 目录实测，含 EuroSAT 真实模型）：

```json
{
  "models_dir": "models",
  "models": [
    {"name": "landcover-seg", "versions": [
      {"version": "v0.0-fake", "fp32": true, "int8": true,
       "class_labels": ["background", "forest", "grassland", "snow", "building"],
       "class_count": 5, "loaded": false, "default_for": ["segmentation"]}]},
    {"name": "landcover-yolo-v11", "versions": [
      {"version": "v1.0", "fp32": true, "int8": true,
       "class_labels": ["AnnualCrop", "Forest", "HerbaceousVegetation", "Highway", "Industrial",
                        "Pasture", "PermanentCrop", "Residential", "River", "Sealake"],
       "class_count": 11, "loaded": false, "default_for": []}]}
  ]
}
```

---

## 2. compute-service（:8002）

### 2.1 双期差异计算

| 项目 | 内容 |
| --- | --- |
| 方法与路径 | `POST /compute/diff` |
| 对应需求 | FR-7.2~7.5（三态划分/后处理/分色/统计）、FR-7.7（边缘伪差异抑制） |
| 内容类型 | application/json |

**功能说明**：对同一要素的两期二值蒙版逐像素计算差异——`added = after & ~before`、`removed = before & ~after`；边缘腐蚀 1px 抑制配准伪差异 → 小区域过滤 → 分色蒙版（默认新增绿/减少红，可配；不变区域透明）→ 双单位统计。纯像素计算不调模型，无状态，支持懒调用。Java 编排：逐要素类别调用。

**请求参数**：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| before_mask_b64 / after_mask_b64 | string | 是 | 两期二值蒙版 PNG base64（须同尺寸，否则 GEO_EXTENT_MISMATCH） |
| element | string | 是 | 要素类别 ID（统计归属） |
| min_area | int | 否 | 最小差异面积阈值（像素，默认 100，FR-7.4） |
| colors | object | 否 | `{"added": "#00FF00", "removed": "#FF0000"}`（默认如此；Java 自 capabilities defaults 下发） |
| geo_transform | float[6] | 否 | 有值时输出 `added_m2`/`removed_m2`（CGCS2000 椭球逐行积分） |

**响应参数**（200）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| diff_mask_png_b64 | string | 差异蒙版 PNG（RGBA 分色）base64 |
| statistics | object | `{added_px, removed_px, net_change_px, change_rate, added_m2, removed_m2}`；`change_rate=(after-before)/before`，before=0 时定义 0（无变化）/1（从无到有） |

**请求示例**：

```bash
curl -X POST http://compute-service:8002/compute/diff \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H 'Content-Type: application/json' -d '{
    "before_mask_b64": "<旧期蒙版 base64>", "after_mask_b64": "<新期蒙版 base64>",
    "element": "forest", "min_area": 100,
    "geo_transform": [104.01, 0.0006, 0.0, 30.73, 0.0, -0.0004]
  }'
```

**实测响应**（100×100 蒙版、方块右移 20px 合成用例）：

```json
{
  "diff_mask_png_b64": "iVBORw0KGgoAAAANSUhEUgAAAGQAAABkCAYAAABw4pVUAAACxElEQVR4Ae3BwQ3A...（截断）",
  "statistics": {
    "added_px": 1044, "removed_px": 1044, "net_change_px": 0, "change_rate": 0.0,
    "added_m2": 2660931.61, "removed_m2": 2660931.61
  }
}
```

### 2.2 轮廓矢量化

| 项目 | 内容 |
| --- | --- |
| 方法与路径 | `POST /compute/vectorize` |
| 对应需求 | FR-1.7/6.4/7.6（矢量化）、V1.2 懒调用约定（result-service 导出时现调） |
| 内容类型 | application/json |

**功能说明**：二值蒙版 → findContours 轮廓 → 面积过滤 → Shapely 抽稀（容差 1px）→ 像素转经纬度（geo_transform 仿射）→ GeoJSON 几何 + 面积（Geod 椭球）+ 中心点 + bbox。bbox 为 Java region 表冗余标量列数据源（需求 14.5 无 PostGIS 方案）。差异任务新增/减少分开调用、分别矢量化。

**请求参数**：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| mask_b64 | string | 是 | 二值蒙版 PNG base64 |
| min_area | float | 否 | 最小区域面积过滤阈值（像素，默认 100） |
| geo_transform | float[6] | 否 | 缺省时输出像素坐标且 `area_m2=null`；有值时输出 CGCS2000 经纬度坐标 |

**响应参数**（200）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| regions | array | 区域列表：`{geojson, area_px, area_m2, centroid, bbox}` |

`regions[]` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| geojson | object | GeoJSON Polygon 几何（经纬度或像素坐标） |
| area_px | float | 多边形面积（Green 定理口径，非像素计数） |
| area_m2 | float\|null | CGCS2000 椭球面积（无 geo_transform 时 null） |
| centroid | float[2] | 中心点 [x, y] |
| bbox | float[4] | `[minx, miny, maxx, maxy]` |

**实测响应**（100×100 蒙版 60×60 方块，geo_transform 同 §2.1 示例）：

```json
{
  "regions": [{
    "geojson": {"type": "Polygon", "coordinates": [[[104.016, 30.722], [104.016, 30.6984], [104.0514, 30.6984], [104.0514, 30.722], [104.016, 30.722]]]},
    "area_px": 3481.0,
    "area_m2": 8872302.85,
    "centroid": [104.0337, 30.7102],
    "bbox": [104.016, 30.6984, 104.0514, 30.722]
  }]
}
```

### 2.3 健康检查

| 项目 | 内容 |
| --- | --- |
| 方法与路径 | `GET /health`（免鉴权） |

**实测响应**：`{"status": "ok", "cpu_cores": 32}`

---

## 3. nlp-service（:8003）

### 3.1 自然语言任务解析

| 项目 | 内容 |
| --- | --- |
| 方法与路径 | `POST /nlp/parse` |
| 对应需求 | FR-9（P2，只解析不执行；确认后由前端调既有分析接口） |
| 内容类型 | application/json |

**功能说明**：将用户自然语言解析为结构化指令（意图 + 地理范围 + 要素类别 + 期次）。NLU 逐请求降级（FR-9.7）：LLM 超时/5xx/结构化输出重试耗尽 → 当次落规则解析（jieba + 句式模板 + 地名词典）并如实标记 `nlu_provider=rule-based`；进程内熔断器（默认 60s 窗口连续 5 次失败断流 30s 后半开试探）。地理编码失败**部分成功降级**（FR-9.8）：location=null + uncertain_fields 标注，交用户确认环节手选范围；仅意图与位置均缺失才返 LOCATION_UNRESOLVED。要素别名/修饰语词表（"建筑群"→building）内置维护（评审 D-02）。

**请求参数**：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| text | string | 是 | 用户自然语言输入 |
| element_catalog | array | 是 | 当前可识别要素目录（Java 自 config-service 下发，J-06）：`[{id, name, model_class_id, enabled}]` |

**响应参数**（200）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| intent | string\|null | `feature_extraction` / `feature_comparison` / `temporal_analysis` |
| location | object\|null | `{raw, bbox, confidence, candidates[]}`；地理编码失败为 null（FR-9.8） |
| elements | string[] | 映射后的要素类别 ID 列表 |
| periods | string[] | 期次标签（如 `["2015","2019"]`） |
| confidence | float | 整体置信度（规则解析恒 0.5，能力受限明示） |
| need_confirm | bool | **恒为 true**（FR-9.4 强制用户确认） |
| unsupported | array | 无法映射的要素表述 `[{raw, reason}]`，reason 含可识别类别列表（FR-9.3） |
| nlu_provider | string | `llm` / `rule-based`（降级如实标记，FR-9.7） |
| uncertain_fields | string[]\|null | 低置信度/降级字段标注（FR-9.4/9.8），无则 null |

**请求示例**：

```bash
curl -X POST http://nlp-service:8003/nlp/parse \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H 'Content-Type: application/json' -d '{
    "text": "识别四川成都金牛区xx街道的建筑群",
    "element_catalog": [
      {"id": "forest", "name": "森林", "model_class_id": 1, "enabled": true},
      {"id": "building", "name": "建筑", "model_class_id": 4, "enabled": true}
    ]
  }'
```

**实测响应**（离线降级链：LLM 与地理编码均未配置，规则解析 + 地名词典定位）：

```json
{
  "intent": "feature_extraction",
  "location": {"raw": "金牛区", "bbox": [103.98, 30.65, 104.13, 30.78], "confidence": 0.5, "candidates": []},
  "elements": ["building"],
  "periods": [],
  "confidence": 0.5,
  "need_confirm": true,
  "unsupported": [],
  "nlu_provider": "rule-based",
  "uncertain_fields": ["location", "intent"]
}
```

**典型错误**：`LOCATION_UNRESOLVED`（422，意图与位置均缺失、完全不可执行）、`NLU_UNAVAILABLE`（503，LLM/地理编码/地名词典均不可用——部署期裁剪形态）。

### 3.2 健康检查

| 项目 | 内容 |
| --- | --- |
| 方法与路径 | `GET /health`（免鉴权） |

**功能说明**：NLU 提供方与熔断器状态（closed/half_open/open，FR-9.7 监控告警）、地理编码提供方可用性。

**实测响应**：

```json
{"status": "ok", "nlu": {"provider": "rule-based", "available": true, "circuit": "closed"},
 "geocoder": {"provider": "nominatim", "available": false}}
```

---

## 4. 错误码表（内部码，设计 §4.6）

统一错误响应：`{"code", "message", "trace_id"}`；Java 侧 PythonErrorDecoder 映射为对外码（J-07）。

| HTTP | code | 场景 | 产生服务 |
| --- | --- | --- | --- |
| 400 | UNSUPPORTED_ELEMENT | 类别超出模型输出（FR-6.6） | infer |
| 400 | IMAGE_DECODE_FAILED | 影像拉取/解码失败（message 携带存储端 HTTP 状态，403 可重签重试） | infer |
| 400 | GEO_EXTENT_MISMATCH | 双期影像尺寸/地理范围不一致、GeoTIFF 与 geo_extent 交叉校验失败 | infer / compute |
| 400 | INPUT_TOO_LARGE | local-cpu 模式输入 >2048px（R-04 兜底） | infer |
| 422 | ALIGNMENT_FAILED | 配准偏差超阈值（FR-1.2 告警路径；自动配准 M6） | infer |
| 422 | LOCATION_UNRESOLVED | 意图与位置均缺失、解析完全不可执行（FR-9.8） | nlp |
| 500 | INFERENCE_FAILED | 推理执行异常（含远程推理超时/失败） | infer |
| 503 | MODEL_NOT_READY | 模型未加载（文件缺失，按 `{name}/{version}/` 组织检查） | infer |
| 503 | NLU_UNAVAILABLE | NLU/地理编码均不可用（FR-9 裁剪场景） | nlp |
| 500 | INTERNAL_ERROR | 兜底（不产生未捕获异常，需求验收 8） | 三服务 |

---

## 5. 契约与联调

- **OpenAPI 快照**：`docs/openapi/{infer,compute,nlp}.json`（CI 与 Java 侧 diff；schema 只改 cv-common，全局要求 #2）；接口变更后执行 `scripts/export_openapi.py` 重导。
- **实测环境说明**：本文示例基于伪模型 v0.0-fake（确定性语义，仅联调用）；真实模型交付后数值以真实推理为准，契约字段不变。
- **Java 侧待确认项**：provider 枚举映射（J-01）、模型双产物 storage_key 约定（J-05）、错误码映射表回传（J-07）——见《Python推理计算服务对Java侧服务接口需求文档》。
