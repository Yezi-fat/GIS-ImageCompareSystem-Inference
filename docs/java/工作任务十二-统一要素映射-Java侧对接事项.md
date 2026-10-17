# Java 侧对接事项：统一要素映射（工作任务十二 / Python V1.3）

| 项目 | 内容 |
| --- | --- |
| 日期 | 2026-10-17 |
| 对应变更 | infer-service 统一要素映射（DW 9 类基准跨模型代号表），接口文档 V1.3 |
| 前置 | 工作任务十一对接文档（层级路由，`tile_z` 下发） |
| 结论速览 | **必改 2 项**（E-1 目录切换 DW 9 要素、E-2 请求瘦身）；**建议 1 项**（E-3 目录同步接口）；附兼容说明 |

---

## 背景

系统现有两套分割模型，识别要素不同：

| 模型 | 数据集 | 类别 | 适用层级 |
| --- | --- | --- | --- |
| sfrs（SegFormer-RS） | Dynamic World | 9 类（含雪） | z ≤ 12（低层级） |
| landcover-yolo-v11（YOLO） | EuroSAT | 10 类（无雪） | z > 12（高层级） |

前端只能展示一套要素 → 以 **Dynamic World 9 类为基准**建立统一要素目录，
跨模型代号映射抽离为 Python L1 配置（`ELEMENTS__MAPPING`，含两模型代号+名称+映射关系，
自定义模型接入仅需增配该模型名条目，无需改代码）。

## E-1【必改】element_catalog 切换为统一 9 要素

数据源：`GET http://infer-service:8001/infer/elements`（V1.3 新增，鉴权同其他接口）。
默认目录（配色由 Java 目录持有，id 建议如下）：

| element（id） | name | sfrs 代号 | YOLO 代号 | 备注 |
| --- | --- | --- | --- | --- |
| water | 水体 | 0 | 8,9（River+SeaLake） | |
| trees | 森林 | 1 | 1（Forest） | |
| grass | 草地 | 2 | 2,5（HerbaceousVegetation+Pasture） | 一对多 |
| flooded_vegetation | 淹水植被 | 3 | —（不支持） | 仅低层级链路 |
| crops | 耕地 | 4 | 0,6（AnnualCrop+PermanentCrop） | 一对多 |
| shrub_and_scrub | 灌丛 | 5 | —（不支持） | 仅低层级链路 |
| built | 建筑 | 6 | 4,7（Industrial+Residential） | 一对多 |
| bare | 裸地 | 7 | —（不支持） | 仅低层级链路 |
| snow_and_ice | 冰雪覆盖 | 8 | —（不支持） | **恢复雪类**（bug-2026-09-28 Q2 遗留，低层级链路可识别） |

**目录数据的 model_class_id 字段在新口径下不再承担路由映射职能**（Python 按模型名自查表），
可保留默认值或弃用（见 E-2）。高层级模型不支持的要素（4 项）在 z>12 场景请求会收到
400 `UNSUPPORTED_ELEMENT`——建议 Java 在编排层按 tile_z 预校验并给出友好提示
（"该要素仅支持 ≤12 级低层级分析"），或前端按能力禁用（需 Java capabilities 扩展，见 E-3）。

## E-2【必改】请求瘦身：业务请求省略 class_mapping 与 model_name/model_version

**新口径（推荐）**：`/infer/segmentation` 业务请求只带
`image_url / geo_extent / tile_z / elements / colors / min_area`——

- **省略 `class_mapping`** → Python 按统一映射表 + 实际路由模型解析代号（V1.3）；
- **省略 `model_name`/`model_version`** → 层级路由才能生效（V1.2 口径：显式指定即视为
  管理端强制指定，不参与路由）。**注意：当前 Java 激活机制会逐请求携带激活模型名/版本，
  这会使路由永远不生效**——业务任务链路请改为不携带（Python L1 默认版本即激活口径；
  若需双写，可将激活结果同步给 Python 部署配置）；
- 兼容说明：继续携带 `class_mapping`（旧口径）功能不受影响——默认模型用 class_mapping，
  低层级路由用 `low_zoom_class_mapping`（或 Python 配置兜底）；但旧口径下前端要素集须
  与两套映射分别对齐，不推荐长期使用。

## E-3【建议】目录同步与能力展示

1. element_catalog 初始化和变更校验可调用 `GET /infer/elements` 对账（要素集、各模型支持性）；
2. （可选）capabilities 增加"要素×模型支持矩阵"或按 tile_z 的可用要素集，供前端禁用不支持项；
3. 前端无需直接对接 `/infer/elements`（经 Java 目录间接生效）。

## 联调验证清单

| # | 场景 | 预期 |
| --- | --- | --- |
| 1 | tile_z=5 + elements=[trees,built]，省略 class_mapping | 200，sfrs 识别，统计非空 |
| 2 | tile_z=15 + elements=[trees,built]，省略 class_mapping | 200，YOLO 识别（EuroSAT 代号自动翻译） |
| 3 | tile_z=15 + elements=[snow_and_ice] | 400 UNSUPPORTED_ELEMENT（YOLO 无雪类） |
| 4 | tile_z=5 + elements=[snow_and_ice] | 200（sfrs 支持雪类） |
| 5 | 请求携带 model_name（旧行为） | 不路由，显式模型生效（回归确认） |
| 6 | 请求携带 class_mapping（旧口径） | 行为同 V1.1/V1.2（回归确认） |
