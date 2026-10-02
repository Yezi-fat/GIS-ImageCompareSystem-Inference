# 影像地图比对系统 —— 业务服务接口文档

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.4 |
| 编写日期 | 2026-09-10 |
| 适用范围 | 影像地图比对系统业务服务后端（Java 微服务群，统一入口 gateway :8080） |
| 依据 | 需求分析文档 V2.6 §7、Java 详细设计 V2.5、docs/openapi/ 快照（springdoc 实测） |
| V1.4 修订 | §1.5 `unsupported` 字段明确为对象数组 `[{raw, reason}]`（与 Python 真实契约对齐，openapi 快照已同步） |
| V1.3 修订 | §0 错误码补录 LOCATION_UNRESOLVED(422)；§1.4 响应补 estimated_shift_px/auto_aligned；§3.4 storage_key 双产物目录前缀约定 |
| V1.2 修订 | 每个接口补全响应参数说明表；任务接口补全五种状态（QUEUED/PROCESSING/SUCCESS/FAILED/CANCELLED）示例 |
| V1.1 修订 | 每个接口补充功能说明与可执行测试示例（测试数据见 `testdata/`，全部示例经运行中全栈实测） |

## 0. 通用约定

- **统一入口**：`http://{gateway}:8080`（本地开发 `http://localhost:8080`）
- **鉴权**：`security.auth.enabled=true`（默认）时，除 `/healthz`、`/api/v1/files/**` 外均需 `Authorization: Bearer <JWT>`；管理接口（`/api/v1/config/**`、`/api/v1/models/**`）另需 ADMIN 角色
- **统一响应包**：`{"code", "message", "task_id", "data", "trace_id"}`（snake_case；成功 code=OK）
- **错误码**：INVALID_INPUT(400) / INPUT_TOO_LARGE(400) / UNSUPPORTED_ELEMENTS(400) / PERIOD_ORDER_INVALID(400) / GEO_EXTENT_MISMATCH(400) / UNAUTHORIZED(401) / FORBIDDEN(403) / TASK_NOT_CANCELLABLE(409) / ALIGNMENT_FAILED(422) / LOCATION_UNRESOLVED(422，自然语言解析意图与位置均缺失、完全不可执行，FR-9.8) / RATE_LIMITED(429) / INFERENCE_UNAVAILABLE(502) / NLU_UNAVAILABLE(503) / INFERENCE_TIMEOUT(504) / INTERNAL_ERROR(500)
- **坐标系**：CGCS2000 经纬度；`tile_range` 与 `geo_extent` 按 360/2^z 线性公式校验一致
- **异步任务**：双期/多期/大图自动返回 202 + task_id，经 `GET /api/v1/tasks/{id}` 轮询（建议间隔见 `poll_hint_ms`）
- **测试准备**：本文测试示例使用 `testdata/` 下的测试数据（见 `testdata/README.md`）；令牌用 `python3 testdata/token-gen.py u1 USER` / `... admin ADMIN` 生成（Windows 用 `testdata/token-gen.ps1`，下文以 `$JWT` / `$JWT_ADMIN` 指代）

```bash
JWT=$(python3 testdata/token-gen.py u1 USER)
JWT_ADMIN=$(python3 testdata/token-gen.py admin ADMIN)
```

**统一响应包字段**（所有接口共用）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| code | 字符串 | 业务码：OK=成功；其余见错误码表 |
| message | 字符串 | 描述信息（错误时为原因） |
| task_id | 字符串/null | 关联任务 ID（仅任务相关错误/场景有值） |
| data | 对象/数组/null | 业务数据（各接口见响应参数表） |
| trace_id | 字符串/null | 链路追踪 ID（排查问题时提供） |

---

## 1. 分析接口

### 1.1 要素识别

`POST /api/v1/feature-extraction`（multipart/form-data；小图同步 200，大图自动转异步 202）

**功能说明**：对单期影像做语义分割，识别用户选定要素类别（可多选）的空间覆盖范围，返回按类别着色的合成蒙版 + 各类别独立蒙版 + 分类别面积统计 + 矢量化明细文件。同步阈值默认长边 ≤1024px（`inference.sync-max-input-size`），超出自动转异步任务。

**请求参数**：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| image | 文件 | 是 | 影像（瓦片场景为前端拼接后的整幅影像；PNG/JPEG/TIFF/GeoTIFF，≤100MB） |
| elements | 字符串 | 是 | 要素类别 ID 列表，逗号分隔（如 `forest,building`；目录见 §3.1） |
| min_area | 数值 | 否 | 最小斑块面积（像素，默认 100） |
| tile_range | 字符串 | 瓦片场景必选 | JSON：`{"z":16,"x_min":..,"x_max":..,"y_min":..,"y_max":..}` |
| geo_extent | 字符串 | 是 | JSON：`[minx,miny,maxx,maxy]`（CGCS2000 经纬度） |

**响应参数**（200 同步成功，`data` 内）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| task_id | 字符串 | 任务 ID（可追溯/导出/复看） |
| inference | 对象 | 推理信息：provider（remote/local_gpu/local_cpu）、model_name、model_version、degraded（是否降级推理） |
| geo_extent | 数组 | 回显请求地理范围 [minx,miny,maxx,maxy] |
| layers | 数组 | 各要素图层（见下行） |
| layers[].element | 字符串 | 要素类别 ID |
| layers[].mask_key | 字符串 | 蒙版对象存储 key（落库口径） |
| layers[].mask_url | 字符串 | 蒙版签名下载 URL（实时签发，默认 2h 有效） |
| layers[].statistics | 对象 | 该类统计：area_px（像素数）、area_m2（地理面积 m²，CGCS2000 椭球）、ratio（占影像比例）、patch_count（斑块数） |
| combined_mask_key / combined_mask_url | 字符串 | 合成蒙版（按类别着色 RGBA）的 key 与签名 URL |
| regions_file_key | 字符串 | 矢量化明细 GeoJSON 文件的 key（导出走 §2.4） |
| elapsed_ms | 数值 | 端到端耗时（毫秒） |

202 转异步时 `data`：`{"task_id", "status":"QUEUED", "poll_hint_ms"}`（轮询间隔建议毫秒数）。

**测试示例**：
```bash
curl -X POST http://localhost:8080/api/v1/feature-extraction \
  -H "Authorization: Bearer $JWT" \
  -F "image=@testdata/images/scene_single.png" \
  -F "elements=forest,building" -F "min_area=100" \
  -F "geo_extent=[104.01,30.69,104.07,30.73]"
```

实测响应 200（节选）：
```json
{
  "code": "OK",
  "data": {
    "task_id": "77eaaf70-9892-4538-9786-ce72689fe4ff",
    "inference": {"provider": "local_cpu", "model_name": "landcover-seg", "model_version": "v2.0", "degraded": false},
    "geo_extent": [104.01, 30.69, 104.07, 30.73],
    "layers": [
      {"element": "forest",   "mask_key": "/77ea.../forest_mask.png",   "mask_url": "http://localhost:8080/api/v1/files/...?token=...", "statistics": {"area_px": 100, "area_m2": 250.0, "ratio": 0.5, "patch_count": 1}},
      {"element": "building", "mask_key": "/77ea.../building_mask.png", "mask_url": "http://...", "statistics": {"area_px": 60, "area_m2": 150.0, "ratio": 0.3, "patch_count": 1}}
    ],
    "combined_mask_key": "/77ea.../combined_mask.png",
    "combined_mask_url": "http://...",
    "regions_file_key": "/77ea.../regions.geojson",
    "elapsed_ms": 553
  }
}
```

### 1.2 双期要素差异比对

`POST /api/v1/feature-comparison`（multipart/form-data；异步为主 202）

**功能说明**：对前后两期影像分别识别要素范围，逐类逐像素计算差异，输出三类图层（旧期范围/新期范围/差异图层）——差异图层中新增区域与减少区域分色标识（默认新增绿 #00FF00、减少红 #FF0000，可配），并给出各类要素的新增/减少/净变化面积与变化率。

**请求参数**：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| before / after | 文件 | 是 | 前后两期影像 |
| elements | 字符串 | 是 | 同上 |
| min_area / auto_align | 数值/布尔 | 否 | 最小斑块面积 / 自动配准开关 |
| tile_range / geo_extent | 字符串 | 同上 | 公共参数 |

**响应参数**（轮询 SUCCESS 后 `data.result` 内）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| task_id | 字符串 | 任务 ID |
| elements | 数组 | 参与比对的要素类别 |
| geo_extent | 数组 | 地理范围 |
| masks | 数组 | 按要素类别组织的图层组 |
| masks[].element | 字符串 | 要素类别 ID |
| masks[].before_mask_key / after_mask_key / diff_mask_key | 字符串 | 旧期/新期/差异蒙版 key |
| masks[].before_mask_url / after_mask_url / diff_mask_url | 字符串 | 对应签名下载 URL（实时签发） |
| diff_legend | 对象 | 差异分色语义：added（新增色）、removed（减少色） |
| statistics | 对象 | 按要素键控的差异统计 |
| statistics.{element}.before_area_px / after_area_px | 数值 | 两期覆盖面积（像素） |
| statistics.{element}.added_px / removed_px | 数值 | 新增/减少面积（像素） |
| statistics.{element}.added_m2 / removed_m2 | 数值 | 新增/减少面积（m²） |
| statistics.{element}.net_change_px | 数值 | 净变化（像素，新-旧） |
| statistics.{element}.change_rate | 数值 | 变化率（净变化/旧期面积） |
| regions_file_key | 字符串 | 矢量化明细文件 key |
| inference | 对象 | 推理信息（同 1.1） |

**测试示例**：
```bash
curl -X POST http://localhost:8080/api/v1/feature-comparison \
  -H "Authorization: Bearer $JWT" \
  -F "before=@testdata/images/scene_2015.png" -F "after=@testdata/images/scene_2016.png" \
  -F "elements=forest,building" -F "geo_extent=[104.01,30.69,104.07,30.73]"
# 返回 202 + task_id，轮询：
curl -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/tasks/{task_id}
```

实测结果（节选）：
```json
{
  "task_id": "0d3acec4-...", "elements": ["forest", "building"], "geo_extent": [104.01, 30.69, 104.07, 30.73],
  "masks": [{"element": "forest", "before_mask_key": "...", "after_mask_key": "...", "diff_mask_key": "...",
             "before_mask_url": "http://...", "after_mask_url": "http://...", "diff_mask_url": "http://..."}],
  "diff_legend": {"added": "#00FF00", "removed": "#FF0000"},
  "statistics": {"forest": {"before_area_px": 100, "after_area_px": 90, "added_px": 5, "removed_px": 15,
                             "added_m2": 12.5, "removed_m2": 37.5, "net_change_px": -10, "change_rate": -0.1}},
  "regions_file_key": "/0d3a.../regions.geojson",
  "inference": {"provider": "local_cpu", "model_name": "landcover-seg", "model_version": "v2.0", "degraded": false}
}
```

### 1.3 多期时序分析

`POST /api/v1/temporal-analysis`（multipart/form-data；恒异步 202）

**功能说明**：接收按时间升序排列的 N 期影像（2≤N≤10 可配），依次计算各期要素覆盖范围 + 相邻期差异图层（共 N-1 组）+ 各期面积序列与相邻期变化量。级联叠加（第 i 期蒙版叠加到第 i~N 期地图）由前端渲染，后端一次性返回全部图层与元数据。任一期失败则整体失败并指明期次，不返回不完整结果。

**请求参数**：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| images | 文件数组 | 是 | 按期次顺序的多期影像 |
| periods | 字符串 | 是 | 与影像一一对应的期次标签（**时间升序**，如 `2015,2016,2019`；颠倒返 PERIOD_ORDER_INVALID） |
| elements / min_area / tile_range / geo_extent | — | 同 1.1 | 各面板须同一地理范围（FR-8.7） |

**响应参数**（`data.result` 内）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| task_id | 字符串 | 任务 ID |
| periods | 数组 | 期次标签序列 |
| geo_extent | 数组 | 地理范围 |
| masks | 数组 | 各期各类蒙版 |
| masks[].period / masks[].element | 字符串 | 期次 / 要素类别 |
| masks[].mask_key / masks[].mask_url | 字符串 | 蒙版 key / 签名 URL |
| masks[].area_px / masks[].area_m2 | 数值 | 该期该类覆盖面积（像素 / m²） |
| diffs | 数组 | 相邻期差异图层（共 N-1×类别数 组） |
| diffs[].from / diffs[].to | 字符串 | 差异的起止期次 |
| diffs[].element | 字符串 | 要素类别 |
| diffs[].diff_mask_key / diffs[].diff_mask_url | 字符串 | 差异蒙版 key / 签名 URL |
| area_series | 对象 | 要素 → 各期面积序列（m²，供面积曲线） |
| period_stats | 数组 | 各期统计：period + 每类 {area_m2, change_m2, change_rate}（首期无变化量） |
| regions_file_key | 字符串 | 矢量化明细文件 key |
| inference | 对象 | 推理信息（同 1.1） |

**测试示例**：
```bash
curl -X POST http://localhost:8080/api/v1/temporal-analysis \
  -H "Authorization: Bearer $JWT" \
  -F "images=@testdata/images/scene_2015.png" -F "images=@testdata/images/scene_2016.png" -F "images=@testdata/images/scene_2019.png" \
  -F "periods=2015,2016,2019" -F "elements=forest" -F "geo_extent=[104.01,30.69,104.07,30.73]"
# 返回 202 + task_id，轮询进度（progress.stage: SEGMENTING/DIFFING/VECTORIZING/UPLOADING）
```

实测结果（节选）：
```json
{
  "task_id": "efb2...", "periods": ["2015", "2016", "2019"], "geo_extent": [104.01, 30.69, 104.07, 30.73],
  "masks": [{"period": "2015", "element": "forest", "mask_key": "...", "mask_url": "http://...", "area_px": 100, "area_m2": 250.0}],
  "diffs": [{"from": "2015", "to": "2016", "element": "forest", "diff_mask_key": "...", "diff_mask_url": "http://..."},
            {"from": "2016", "to": "2019", "element": "forest", "diff_mask_key": "...", "diff_mask_url": "http://..."}],
  "area_series": {"forest": [250.0, 250.0, 250.0]},
  "period_stats": [{"period": "2015", "forest": {"area_m2": 250.0}},
                   {"period": "2016", "forest": {"area_m2": 250.0, "change_m2": 0.0, "change_rate": 0.0}}],
  "regions_file_key": "...", "inference": {...}
}
```

### 1.4 端到端变化检测

`POST /api/v1/change-detection`（multipart/form-data；小图可同步）

**功能说明**：变化检测模型直接对两期影像输出变化蒙版（不经要素识别），抗光照/季节/云影等非语义干扰，适用于未限定要素类别的通用变化发现。返回变化蒙版 + 变化概率图（前端可按阈值动态调整）+ 变化统计 + 矢量化明细。

**请求参数**：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| before / after | 文件 | 是 | 两期影像 |
| threshold / min_area / auto_align | 数值/布尔 | 否 | 变化阈值（默认 0.5）等 |
| tile_range / geo_extent | 字符串 | 同上 | 公共参数 |

**响应参数**（`data.result` 内）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| task_id | 字符串 | 任务 ID |
| geo_extent | 数组 | 地理范围 |
| mask_key / mask_url | 字符串 | 变化蒙版 key / 签名 URL |
| probmap_key / probmap_url | 字符串 | 变化概率图 key / 签名 URL（可选输出） |
| statistics | 对象 | 变化统计：change_count（变化区域数）、total_area_px、total_area_m2（总面积）等 |
| estimated_shift_px | 数值 | 配准校验实测整体平移量（像素，FR-1.2 告警路径） |
| auto_aligned | 布尔 | 是否自动配准（本期恒 false，自动配准列 M6；auto_align=true 且偏差超阈值时任务以 ALIGNMENT_FAILED(422) 失败） |
| regions_file_key | 字符串 | 矢量化明细文件 key |
| inference | 对象 | 推理信息（同 1.1） |

**测试示例**：
```bash
curl -X POST http://localhost:8080/api/v1/change-detection \
  -H "Authorization: Bearer $JWT" \
  -F "before=@testdata/images/scene_2015.png" -F "after=@testdata/images/scene_2019.png" \
  -F "threshold=0.5" -F "geo_extent=[104.01,30.69,104.07,30.73]"
```

实测结果：`{"task_id":"2caf...","geo_extent":[...],"mask_key":"...","mask_url":"http://...","probmap_key":"...","probmap_url":"http://...","statistics":{"change_count":1,"total_area_m2":100.0},"regions_file_key":"...","inference":{...}}`

### 1.5 自然语言任务解析

`POST /api/v1/nl-task/parse`（application/json；FR-9，只解析不执行）

**功能说明**：将用户自然语言（如"识别四川成都金牛区xx街道的建筑群"）解析为结构化指令（意图 + 地理范围 + 要素类别 + 期次），**须经用户确认后才执行**——地图跳转、瓦片采集、正式分析由前端按结构化指令调用 1.1~1.3 既有接口完成。`need_confirm` 恒为 true；要素无法映射时在 `unsupported` 中说明并列出可识别类别；地理编码失败时 `location=null` 且 `uncertain_fields` 标注，由用户手选范围（FR-9.8）。

**请求参数**：`{"text": "识别四川成都金牛区xx街道的建筑群"}`（text 必填）

**响应参数**（`data` 内）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| intent | 字符串 | 意图类型：feature_extraction / feature_comparison / temporal_analysis |
| location | 对象/null | 地理范围：raw（原始地名）、bbox（经纬度边界）、confidence、候选 candidates（歧义时供用户选择）；地理编码失败为 null |
| elements | 数组 | 映射到要素目录的类别 ID |
| periods | 数组 | 期次标签（多期意图时） |
| confidence | 数值 | 整体解析置信度 |
| need_confirm | 布尔 | 恒 true（FR-9.4 强制用户确认） |
| unsupported | 数组 | 无法映射的要素表述，对象数组 `[{raw, reason}]`：raw=原始表述，reason=无法映射原因（含可识别类别列表，FR-9.3） |
| nlu_provider | 字符串 | llm / rule-based（降级实现标记，FR-9.7） |
| uncertain_fields | 数组/null | 不确定字段标注（如 location，FR-9.8） |

**测试示例**：
```bash
curl -X POST http://localhost:8080/api/v1/nl-task/parse \
  -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
  -d '{"text":"识别四川成都金牛区xx街道的建筑群"}'
```

实测响应 200：
```json
{
  "code": "OK",
  "data": {
    "intent": "feature_extraction",
    "location": {"raw": "四川成都金牛区", "bbox": [104.01, 30.69, 104.07, 30.73], "confidence": 0.86, "candidates": []},
    "elements": ["building"], "periods": [], "confidence": 0.82,
    "need_confirm": true, "unsupported": [], "nlu_provider": "llm", "uncertain_fields": null
  }
}
```
说明：`nlp.enabled=false` 或 Python 不可用 → 503 NLU_UNAVAILABLE。

---

## 2. 任务接口

### 2.1 任务详情

`GET /api/v1/tasks/{task_id}`

**功能说明**：异步任务的唯一查询入口——状态、进度（多期任务按期次细分）、结果。结果中的图层 URL 为**实时签名**（落库只存 key），历史任务任意时刻重新加载均可用。QUEUED 态返回 `queue_position`/`estimated_wait_ms`。

**响应参数**（`data` 内）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | 字符串 | 任务 ID |
| task_type | 字符串 | FEATURE_EXTRACTION / FEATURE_COMPARISON / TEMPORAL_ANALYSIS / CHANGE_DETECTION |
| status | 字符串 | QUEUED / PROCESSING / SUCCESS / FAILED / CANCELLED |
| progress | 对象/null | 进度：total_periods（总期数）、completed_periods（已完成）、stage（SEGMENTING/DIFFING/VECTORIZING/UPLOADING） |
| queue_position | 数值/null | 排队位置（仅 QUEUED 态，FR-2.5） |
| estimated_wait_ms | 数值/null | 预计等待毫秒（仅 QUEUED 态） |
| payload | 对象 | 任务参数快照（影像引用、要素类别、阈值等） |
| provider | 字符串/null | 实际推理提供方（REMOTE/LOCAL_GPU/LOCAL_CPU） |
| model_name / model_version | 字符串/null | 实际使用的模型名与版本（FR-3.3 可追溯） |
| degraded | 布尔 | 是否降级推理 |
| result | 对象/null | 结果（SUCCESS 时返回，结构按任务类型见 §1 各接口响应参数表） |
| error_code / error_message | 字符串/null | 失败原因（FAILED 时） |
| created_by | 字符串 | 创建人 |
| created_at / finished_at | 时间戳/null | 创建/完成时间 |

**测试示例**（task_id 从分析接口返回中捕获，或从任务列表取一条）：
```bash
# 方式一：先发起一次异步分析（双期比对），从 202 响应拿到 task_id
TID=$(curl -s -X POST -H "Authorization: Bearer $JWT" \
  http://localhost:8080/api/v1/feature-comparison \
  -F "before=@testdata/images/scene_2015.png" -F "after=@testdata/images/scene_2016.png" \
  -F "elements=forest" -F "geo_extent=[104.01,30.69,104.07,30.73]" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['task_id'])")
echo "task_id=$TID"

# 轮询任务详情（status 会经历 QUEUED→PROCESSING→SUCCESS）
curl -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/tasks/$TID

# 方式二：从历史任务中取一个
TID=$(curl -s -H "Authorization: Bearer $JWT" "http://localhost:8080/api/v1/tasks?page=0&size=1" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['content'][0]['id'])")
curl -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/tasks/$TID
```

**五种状态实测响应**（`data` 节选）：

QUEUED（排队中，含排队位置）：
```json
{"id":"...","task_type":"FEATURE_COMPARISON","status":"QUEUED","progress":null,
 "queue_position":0,"estimated_wait_ms":0,"result":null,"finished_at":null}
```

PROCESSING（处理中，含期次细分进度）：
```json
{"id":"...","task_type":"TEMPORAL_ANALYSIS","status":"PROCESSING",
 "progress":{"total_periods":3,"completed_periods":2,"stage":"DIFFING"},
 "queue_position":null,"estimated_wait_ms":null,"result":null,"finished_at":null}
```

SUCCESS（成功，含完整结果）：
```json
{"id":"...","task_type":"FEATURE_COMPARISON","status":"SUCCESS","progress":{"total_periods":2,"completed_periods":2,"stage":"UPLOADING"},
 "result":{"task_id":"...","masks":[...],"diff_legend":{...},"statistics":{...},"regions_file_key":"..."},
 "provider":"LOCAL_CPU","model_name":"landcover-seg","model_version":"v2.0","degraded":false,
 "finished_at":"2026-09-10T14:05:12Z"}
```

FAILED（失败，含错误码与原因）：
```json
{"id":"...","task_type":"FEATURE_EXTRACTION","status":"FAILED",
 "result":null,"error_code":"INTERNAL_ERROR","error_message":"执行重试耗尽: 推理调用失败...",
 "finished_at":"..."}
```

CANCELLED（已取消）：
```json
{"id":"...","task_type":"FEATURE_EXTRACTION","status":"CANCELLED",
 "result":null,"error_code":null,"error_message":null,"finished_at":"..."}
```

### 2.2 任务列表

`GET /api/v1/tasks?task_type=&page=&size=`

**功能说明**：分页查询任务列表，支持按类型过滤。数据隔离：普通用户仅见本人任务，ADMIN 可查全部；鉴权关闭（auth.enabled=false）时匿名共享。

**请求参数**（query）：

| 参数 | 类型 | 必选 | 说明 |
| --- | --- | --- | --- |
| task_type | 字符串 | 否 | 按类型过滤 |
| page / size | 数值 | 否 | 页码（从 0 起）/ 每页条数（默认 20） |

**响应参数**（`data` 内，Spring Data Page 结构）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| content | 数组 | 任务条目（字段同 2.1 任务详情） |
| total_elements / total_pages | 数值 | 总条数 / 总页数 |
| size / number | 数值 | 每页条数 / 当前页码 |
| pageable / sort / last 等 | — | 分页元数据（Spring Data 标准） |

**测试示例**（无需 task_id，直接分页查询）：
```bash
curl -H "Authorization: Bearer $JWT" "http://localhost:8080/api/v1/tasks?task_type=FEATURE_COMPARISON&page=0&size=5"
```

实测响应（节选）：`{"code":"OK","data":{"content":[{"id":"...","task_type":"FEATURE_COMPARISON","status":"SUCCESS",...}],"total_elements":1,"total_pages":1,"size":5,"number":0,...}}`

### 2.3 任务取消

`DELETE /api/v1/tasks/{task_id}`

**功能说明**：取消排队中的任务（仅 QUEUED 态，FR-2.8）；多期任务取消不保留中间结果。非 QUEUED 态返回 409 TASK_NOT_CANCELLABLE。

**响应参数**：成功 200 + 统一包（`{"code":"OK"}`）；失败 409 `{"code":"TASK_NOT_CANCELLABLE","message":"任务状态已变化：期望 QUEUED，实际 PROCESSING","trace_id":"..."}`。

**测试示例**（先发起一次多期分析制造排队任务，立即取消）：
```bash
# 发起多期时序分析（异步），立即取消——空队列时 Worker 消费很快，
# 取消成功窗口很短属预期行为；要稳定观察 QUEUED 态可先在队列里压几个任务
TID=$(curl -s -X POST -H "Authorization: Bearer $JWT" \
  http://localhost:8080/api/v1/temporal-analysis \
  -F "images=@testdata/images/scene_2015.png" -F "images=@testdata/images/scene_2016.png" \
  -F "periods=2015,2016" -F "elements=forest" -F "geo_extent=[104.01,30.69,104.07,30.73]" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['task_id'])")

curl -X DELETE -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/tasks/$TID

# 确认取消后状态
curl -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/tasks/$TID
```

### 2.4 GeoJSON 导出

`GET /api/v1/tasks/{task_id}/export`

**功能说明**：导出任务的多边形明细（regions.geojson，含覆盖/新增/减少/变化区域）。懒矢量化模式（persist.vectorize=lazy）下若任务期未矢量化，导出时现算并回填。权属校验：仅创建人或 ADMIN 可导出；开关 `export.geojson.enabled=false` 时 403。

**响应参数**（`data` 内）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| download_url | 字符串 | GeoJSON 签名下载 URL（默认 2h 有效，过期重新调用本接口即得新 URL） |
| expires_at | 时间戳 | URL 过期时间 |

**测试示例**（先跑一个任务并等它 SUCCESS，再导出）：
```bash
# 发起双期比对并轮询至 SUCCESS（约 10~20s）
TID=$(curl -s -X POST -H "Authorization: Bearer $JWT" \
  http://localhost:8080/api/v1/feature-comparison \
  -F "before=@testdata/images/scene_2015.png" -F "after=@testdata/images/scene_2016.png" \
  -F "elements=forest" -F "geo_extent=[104.01,30.69,104.07,30.73]" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['task_id'])")
while true; do
  S=$(curl -s -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/tasks/$TID \
      | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['status'])")
  [ "$S" = "SUCCESS" ] && break; sleep 5
done

# 导出 GeoJSON
curl -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/tasks/$TID/export

# 下载导出的 GeoJSON 文件
URL=$(curl -s -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/tasks/$TID/export \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['download_url'])")
curl -O "$URL"
```

实测响应：
```json
{"code":"OK","data":{"download_url":"http://localhost:8080/api/v1/files/0d3a.../regions.geojson?token=...","expires_at":"2026-09-10T14:11:29Z"}}
```

---

## 3. 目录/能力/配置/模型

### 3.1 要素类别目录

`GET /api/v1/elements`

**功能说明**：查询当前可识别的要素类别目录（前端渲染选择器；类别与分割模型输出类别的映射含在 `model_class_id`）。类别扩展无需前端发版。

**响应参数**（`data` 数组每项）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | 字符串 | 要素类别 ID（分析接口 elements 参数用它） |
| name | 字符串 | 显示名 |
| color | 字符串 | 叠加显示颜色 #RRGGBB |
| model_class_id | 数值 | 分割模型输出类别 ID 映射（FR-6.2） |
| enabled | 布尔 | 是否启用 |

**测试示例**：`curl -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/elements`

实测响应：`{"code":"OK","data":[{"id":"forest","name":"森林","color":"#228B22","model_class_id":1,"enabled":true},{"id":"grassland","name":"草地","color":"#9ACD32","model_class_id":2,"enabled":true},{"id":"snow","name":"雪山覆盖","color":"#F0F8FF","model_class_id":3,"enabled":true},{"id":"building","name":"建筑","color":"#CD853F","model_class_id":4,"enabled":true}]}`

### 3.2 系统能力集

`GET /api/v1/capabilities`

**功能说明**：系统能力探测——功能开关状态 + 当前推理/存储提供方 + 鉴权开关 + 瓦片矩阵参数 + 默认参数集，前端按能力动态渲染功能入口（如 NLP 关闭时隐藏输入框）。某下游状态聚合失败时对应 provider="unknown" 且 partial=true（不整体 5xx）。

**响应参数**（`data` 内）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| features | 对象 | 功能开关：nlp.enabled / change-detection.enabled / export-geojson.enabled / websocket.enabled |
| inference_provider | 字符串 | 当前推理提供方：REMOTE / LOCAL_GPU / LOCAL_CPU / unknown |
| storage_provider | 字符串 | 当前存储提供方：oss / obs / local / unknown |
| auth_enabled | 布尔 | 鉴权总开关状态（FR-10.7，前端据此决定是否走登录流程） |
| tile_matrix | 对象 | 瓦片矩阵参数：origin（原点）、span_base（跨度基数 360）、start_level（起始级）——前后端同源下发（FR-10.6） |
| defaults | 对象 | 默认参数集：threshold（变化检测默认阈值）、min_area（最小斑块面积）、diff_colors（差异分色 added/removed） |
| partial | 布尔 | 是否部分降级（某下游聚合失败时为 true） |

**测试示例**：`curl -H "Authorization: Bearer $JWT" http://localhost:8080/api/v1/capabilities`

实测响应：
```json
{"code":"OK","data":{
  "features":{"nlp.enabled":true,"change-detection.enabled":true,"export-geojson.enabled":true,"websocket.enabled":false},
  "inference_provider":"LOCAL_CPU","storage_provider":"local","auth_enabled":true,
  "tile_matrix":{"origin":[-180,90],"span_base":360.0,"start_level":1},
  "defaults":{"threshold":0.5,"min_area":100,"diff_colors":{"added":"#00FF00","removed":"#FF0000"}},
  "partial":false}}
```

### 3.3 运行配置（管理员）

**功能说明**：运行配置在线查看/修改——持久化于数据库（重启不丢）、修改热生效（<1s，无需重启与发版）、修改前合法性校验（类型/范围/组合约束，任一项非法整组拒绝）、所有修改写审计日志。`GET /api/v1/config/inference` 即"查看当前推理提供方配置"的兼容入口。

| 接口 | 功能 |
| --- | --- |
| `GET /api/v1/config/{group}` | 按组查看（当前值/默认值/是否被修改）；组：inference/task/persist/storage/feature/security/cache/features |
| `PUT /api/v1/config/{group}` | 按组修改（热生效+审计） |
| `POST /api/v1/config/{group}/reset` | 恢复该组默认值 |

**响应参数**：

- GET（`data` 内）：`group`（组名）、`items`（key → `{value, default_value, modified, meta}` 三态对象）
- PUT/reset：统一包 `{"code":"OK"}`；非法值 400 `{"code":"INVALID_INPUT","message":"...取值须为.../组合约束不满足..."}`

**测试示例**：
```bash
# 查看 task 组
curl -H "Authorization: Bearer $JWT_ADMIN" http://localhost:8080/api/v1/config/task
# 修改多期期数上限为 8（热生效）
curl -X PUT -H "Authorization: Bearer $JWT_ADMIN" -H 'Content-Type: application/json' \
  -d '{"task.temporal-max-periods":"8"}' http://localhost:8080/api/v1/config/task
# 非法值（下限 2，写 1）→ 实测 400
# 恢复默认
curl -X POST -H "Authorization: Bearer $JWT_ADMIN" http://localhost:8080/api/v1/config/task/reset
```

实测 GET 响应（节选）：`{"code":"OK","data":{"group":"task","items":{"task.temporal-max-periods":{"value":"10","default_value":"10","modified":false,"meta":null},...}}}`

### 3.4 本地模型版本管理（管理员，FR-3.5）

**功能说明**：本地推理模型的版本注册与热切换——登记时模型文件经存储服务取出并下发到推理服务模型目录 `{name}/{version}/`（固定命名 `fp32.onnx` / `int8.onnx`）；激活即切换（写运行配置热生效，后续推理请求携带新模型名/版本，无需发版）；回滚 = 重新激活旧版本（历史版本记录保留为 RETIRED 不删除）。

**storage_key 约定（FR-3.2 双产物）**：指向**目录前缀**（如 `models/landcover-seg/v2.2/`），其下须含 `fp32.onnx`（必需，缺失拒绝登记）与 `int8.onnx`（可选，缺失仅告警）；兼容单文件形态（`xxx.onnx`，视作 fp32 单产物）。

| 接口 | 功能 |
| --- | --- |
| `GET /api/v1/models` | 模型版本列表 |
| `POST /api/v1/models` | 登记（body：`{"model_name","model_version","model_type":"SEG/CHANGE","storage_key"}`，storage_key 为目录前缀或单文件，见上） |
| `PUT /api/v1/models/active` | 激活切换（body：`{"model_name","model_version"}`） |

**响应参数**（`data` 每项，模型版本对象）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| model_name | 字符串 | 模型名（landcover-seg / change-detection） |
| model_version | 字符串 | 版本号 |
| model_type | 字符串 | SEG（分割）/ CHANGE（变化检测） |
| storage_key | 字符串 | 模型文件在对象存储中的位置 |
| status | 字符串 | REGISTERED / ACTIVE / RETIRED |
| activated_at | 时间戳/null | 激活时间 |
| created_by / created_at | 字符串/时间戳 | 登记人与时间 |

**测试示例**（先用任意内部调用把模型文件放入存储，storage_key 指向其目录前缀；内网交付时模型文件由运维直接放置模型目录后可仅登记元数据）：
```bash
# 登记（storage_key 为目录前缀，其下须含 fp32.onnx，可选 int8.onnx）
curl -X POST -H "Authorization: Bearer $JWT_ADMIN" -H 'Content-Type: application/json' \
  -d '{"model_name":"landcover-seg","model_version":"v2.2","model_type":"SEG","storage_key":"models/landcover-seg/v2.2/"}' \
  http://localhost:8080/api/v1/models
# 激活
curl -X PUT -H "Authorization: Bearer $JWT_ADMIN" -H 'Content-Type: application/json' \
  -d '{"model_name":"landcover-seg","model_version":"v2.2"}' http://localhost:8080/api/v1/models/active
# 列表
curl -H "Authorization: Bearer $JWT_ADMIN" http://localhost:8080/api/v1/models
```

实测列表响应：`{"code":"OK","data":[{"model_name":"landcover-seg","model_version":"v2.2","model_type":"SEG","storage_key":"models/landcover-seg-v2.2.onnx","status":"ACTIVE","activated_at":"...","created_by":"admin","created_at":"..."},{"model_version":"v2.2"...,"status":"RETIRED",...}]}`

---

## 4. 其他

| 接口 | 功能说明 |
| --- | --- |
| `GET /healthz` | 服务健康聚合（gateway；免鉴权）。**测试示例**：`curl http://localhost:8080/healthz` → `{"status":"UP"}` |
| `GET /api/v1/files/{key}?token=` | local 存储模式文件下载（HMAC 时效 token 鉴权，免 JWT；token 由签名 URL 携带）。**测试示例**：取任一任务结果中的 `mask_url` 直接 `curl -O` 下载 |
| `WS /ws/progress` | STOMP 进度推送（features.websocket.enabled=true 时启用，订阅 `/topic/progress/{taskId}`；关闭时纯轮询）。**测试示例**：开启开关后用任意 STOMP 客户端连接 `ws://<task-service>:8081/ws/progress` 订阅 |
| `GET /actuator/prometheus`（各服务内网端口） | Prometheus 指标：inference_latency_seconds{provider} / inference_degraded_total / task_queue_depth / storage_usage_ratio / config_change_total 等。**测试示例**（容器网络内）：`docker exec mapchange-task-service curl -s localhost:8081/actuator/prometheus \| grep task_queue_depth` |
