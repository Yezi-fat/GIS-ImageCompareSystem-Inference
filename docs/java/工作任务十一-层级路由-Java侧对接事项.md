# Java 侧对接事项：层级路由（工作任务十一 / Python V1.2）

| 项目 | 内容 |
| --- | --- |
| 日期 | 2026-10-17 |
| 对应变更 | infer-service 层级路由（瓦片层级 → 模型选择），接口文档 V1.2 |
| 结论速览 | **必改 1 项**（R-1 下发 tile_z）；**建议 1 项**（R-2 低层级映射下发）；其余为认知项 |

---

## 背景

YOLO 检测模型在低层级瓦片（1~12 级，地面分辨率低）识别能力弱。新增 Dynamic World
数据集训练的 SegFormer-RemoteSensing 逐像素分割模型（已部署为 `sfrs/v1.0`，
双产物齐备，ONNX 元数据内嵌 9 类类别表），infer-service 按**瓦片层级路由**：

```
请求带 tile_z 且未显式指定 model_name/model_version：
  tile_z ≤ 12（MODELS__ROUTING__ZOOM_THRESHOLD，可配）→ sfrs（SegFormer-RS）
  tile_z > 12                                            → 默认模型（YOLO 系）
未带 tile_z 或显式指定模型 → 不路由（行为与 V1.1 一致）
```

## R-1【必改】`/infer/segmentation` 请求下发 `tile_z`

- 瓦片场景：取 `tile_range.z`；单图上传场景：取地图当前层级（无层级概念时可省略，
  省略则不路由、恒用 YOLO 默认模型——低层级影像识别效果会差，建议尽量下发）；
- 取值范围 1~18（超范围 422 schema 校验）；
- 路由结果经响应 `model_info.name/version` 如实返回（`sfrs` / `landcover-yolo-v11`），
  任务记录与前端展示直接复用现有字段即可。

## R-2【建议】下发 `low_zoom_class_mapping`（低层级模型类别映射）

路由命中 sfrs 时，`class_mapping` 不适用（两套模型类别表不同），生效映射为：

1. 请求 `low_zoom_class_mapping`（Java 自 element_catalog 计算下发，**推荐**——
   保持"目录单一事实源"架构）；或
2. 缺省时 infer L1 配置兜底：`{"forest":1, "grassland":2, "building":6, "snow":8}`
   （Dynamic World：1=trees 2=grass 6=built 8=snow_and_ice）。

Dynamic World 9 类全表：`0=water 1=trees 2=grass 3=flooded_vegetation 4=crops
5=shrub_and_scrub 6=built 7=bare 8=snow_and_ice`。

**注意**：
- 要素不在生效映射中 → 400 `UNSUPPORTED_ELEMENT`（message 注明 low_zoom_class_mapping）；
- 若 element_catalog 需要携带"每要素两个类别 ID"（YOLO / DW 各一），可用目录扩展字段
  或 Java 侧内置转换表（Java 自决，Python 无感）；
- **意外收获**：Dynamic World 含 `snow_and_ice`（8）——低层级链路下"雪山覆盖"可识别
  （此前 EuroSAT 模型无雪类被迫下架，bug-2026-09-28 Q2），目录可考虑恢复该要素并配置
  `snow→8` 的低层级映射。

## R-3【认知项】模型管理与展示

- `GET /infer/models` 已可发现 `sfrs/v1.0`（类别表 9 类、class_count=9）；
- sfrs 为**路由专用模型**，不参与 Java 模型激活切换（`PUT /api/v1/models/active`
  管的是默认模型 YOLO 系）；路由低层级模型的版本更换走 Python L1 配置
  （`MODELS__ROUTING__LOW_ZOOM_VERSION`，重启生效）；
- 路由阈值调整走 Python L1 配置（`MODELS__ROUTING__ZOOM_THRESHOLD`，重启生效）；
  如需 Java 运行配置热生效，后续版本再议。

## R-4【认知项】预处理口径

sfrs 路由链路的影像预处理按 HF SegFormer 惯例（/255 后减 ImageNet 均值除标准差，
L1 可配 `MODELS__ROUTING__LOW_ZOOM_NORMALIZE_MEAN/STD`），Java 无需处理，知悉即可。

## 联调验证清单

| # | 场景 | 预期 |
| --- | --- | --- |
| 1 | tile_z=5 请求 | `model_info.name=sfrs`，统计口径正常 |
| 2 | tile_z=15 请求 | `model_info.name` 为默认 YOLO 模型 |
| 3 | 不带 tile_z | 行为同 V1.1（默认模型） |
| 4 | tile_z=5 + 显式 model_name | 显式模型生效，不路由 |
| 5 | tile_z=5 + 要素不在低层级映射 | 400 UNSUPPORTED_ELEMENT |
| 6 | tile_z=19 | 422 schema 校验失败 |
