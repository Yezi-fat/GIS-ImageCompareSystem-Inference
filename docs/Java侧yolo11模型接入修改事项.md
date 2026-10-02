# Java 侧修改事项：接入 yolo11 v1.0 真实模型

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0 |
| 编写日期 | 2026-09-22 |
| 发起方 | Python 推理计算服务侧 |
| 前置阅读 | 《Python推理计算服务接口文档.md》§1.1、§4 |
| 紧急度 | 高——当前 Java 配置指向伪模型，与 Python 部署的 yolo11 不一致，要素识别全部报错（`模型输出通道数 5 与配置 class_ids（max=80）不一致`，INFERENCE_FAILED） |

## 1. 背景与现状

- Python 侧已于 2026-09-21 完成真实模型接入并部署：**yolo11 v1.0**（Ultralytics YOLO11x，COCO 80 类检测模型），容器运行中，自测通过。
- 检测输出经 Python 侧 `yolo_decode` 栅格化为 81 通道概率图（通道 0~79 = COCO 类别序号**直用**，通道 80 = 背景残差），**对外接口契约不变**（/infer/segmentation 请求/响应字段无任何变化）。
- 当前 Java 侧 `config.app_config` 仍下发联调期伪模型（`inference.local-seg-model-name=landcover-seg`、`inference.local-seg-model-version=v0.0-fake`），导致每次识别请求打到 5 通道伪模型、与 Python 侧 81 通道配置错配而被拒绝。**本错误是 Python 侧防错配保护，非缺陷。**

## 2. Java 侧必改事项（2 项）

### J-A1 激活分割模型 yolo11/v1.0（必改）

经模型管理 API（推荐，走 audit）：

```bash
# ① 登记（modelType=SEG；storageKey 按贵侧约定填写，Python 侧实际从挂载卷
#    /models/yolo11/v1.0/{fp32,int8}.onnx 读取，storageKey 仅作元数据）
POST /api/v1/models
{"modelName": "yolo11", "modelVersion": "v1.0", "modelType": "SEG",
 "storageKey": "models/yolo11/v1.0"}

# ② 激活（写 L2 键对 inference.local-seg-model-name/version 并广播，
#    analysis-service 热重解析，无需重启任何服务）
PUT /api/v1/models/active
{"modelName": "yolo11", "modelVersion": "v1.0"}
```

或直接改库（绕过 audit，联调期可接受）：

```sql
UPDATE config.app_config SET value='yolo11', updated_by='admin', updated_at=now()
 WHERE config_key='inference.local-seg-model-name';
UPDATE config.app_config SET value='v1.0',   updated_by='admin', updated_at=now()
 WHERE config_key='inference.local-seg-model-version';
```

> **不要动** `inference.local-change-model-name/version`（变化检测仍用 v0.0-fake 伪模型——
> yolo11 是单输入检测模型，无法承担双输入端到端变化检测；变化检测真实模型待交付）。
> 双期比对业务（FR-7）走 segmentation×2 + /compute/diff 路径，不受此限制。

### J-A2 要素目录 model_class_id 重映射（必改）

yolo11 为 COCO 80 类模型，`element_catalog.model_class_id` 取值规则：

- **取值 = COCO 类别序号直用（0~79）**；
- **80 保留为背景通道，禁止映射任何业务要素**（映射了会把整幅背景识别为该要素）；
- 现目录值 1/2/3/4 在新模型下会被解读为 bicycle/car/motorcycle/airplane，语义错误，必须重新映射。

**重要语义提醒**：COCO 80 类为通用目标（人/车/动物/物品），**不含森林、草地、雪山、建筑等遥感地物类别**。该模型可用于全链路真实推理测试，但地物类别的最终业务识别仍需模型团队交付按地物类别训练的模型。测试期映射建议按测试影像内容选择（示例）：

| 要素 | 建议 model_class_id（示例） | 说明 |
| --- | --- | --- |
| building 建筑 | 2（car）或 5（bus） | 以影像中车辆为目标做链路验证 |
| forest 森林 | —（无可映射类） | 测试期可临时禁用（enabled=false） |
| grassland 草地 | —（无可映射类） | 同上 |
| snow 雪山覆盖 | —（无可映射类） | 同上 |

COCO 类别速查（完整 80 类见附录）：0=person, 1=bicycle, 2=car, 3=motorcycle, 4=airplane, 5=bus, 6=train, 7=truck, 8=boat, 9=traffic light, 14=bird, 15=cat, 16=dog, 24=backpack, 56=chair, 57=couch, 59=bed, 62=tv, 67=cell phone ...

要素目录当前只有查询接口（GET /api/v1/elements），无写接口——请 Java 侧二选一：

```sql
-- 方式一：直接改库（示例：building→2=car；其余按上表处理）
UPDATE config.element_catalog SET model_class_id=2 WHERE id='building';
UPDATE config.element_catalog SET enabled=false WHERE id IN ('forest','grassland','snow');
```

方式二：补一个要素目录管理接口（PUT /api/v1/elements/{id}），从源头维护。

## 3. 生效机制（无需重启）

- 模型激活：激活 API 写 L2 键对 → `ConfigGroupRefreshedEvent` 广播 → analysis-service 热重解析，**下一次请求即生效**；Python infer-service 按请求 model_name/version 懒加载，**任何侧均无需重启**。
- 要素目录直改库后：若 Java 侧有目录缓存请触发刷新（或重启 config-service / analysis-service）。
- 验证：改完后前端发起一次要素识别，期望返回 200 且 `model_info={name:"yolo11", version:"v1.0", provider:"local-cpu"}`，蒙版与统计非空。

## 4. Python 侧已就绪事项（信息同步，无需 Java 动作）

| 项 | 值 |
| --- | --- |
| 接口契约 | 不变（OpenAPI 快照无差异，schema/错误码均未改） |
| class_ids | [0..80]（80=背景保留），配置 `MODELS__SEGMENTATION__CLASS_IDS` |
| 检测阈值 | 置信度 0.25 / NMS IoU 0.45（L1 配置 `MODELS__SEGMENTATION__{CONF,NMS}_THRESHOLD`，Python 侧运维项，Java 无需下发） |
| 通道保护 | 模型输出通道数与 class_ids 不一致 → INFERENCE_FAILED（防错切类别，本次报错即此机制） |
| int8 产物 | 交付的静态量化 int8 输出全零已弃用（备份 int8.delivered-broken.onnx），CPU 路径用 Python 侧动态量化重制版——建议模型团队复核量化流水线 |

## 5. 附录：COCO 80 类完整对照

```
0 person      1 bicycle     2 car        3 motorcycle  4 airplane
5 bus         6 train       7 truck      8 boat        9 traffic light
10 fire hydrant  11 stop sign  12 parking meter  13 bench   14 bird
15 cat        16 dog        17 horse     18 sheep      19 cow
20 elephant   21 bear       22 zebra     23 giraffe    24 backpack
25 umbrella   26 handbag    27 tie       28 suitcase   29 frisbee
30 skis       31 snowboard  32 sports ball  33 kite    34 baseball bat
35 baseball glove  36 skateboard  37 surfboard  38 tennis racket  39 bottle
40 wine glass 41 cup        42 fork      43 knife      44 spoon
45 bowl       46 banana     47 apple     48 sandwich   49 orange
50 broccoli   51 carrot     52 hot dog   53 pizza      54 donut
55 cake       56 chair      57 couch     58 potted plant  59 bed
60 dining table  61 toilet  62 tv        63 laptop     64 mouse
65 remote     66 keyboard   67 cell phone  68 microwave  69 oven
70 toaster    71 sink       72 refrigerator  73 book    74 clock
75 vase       76 scissors   77 teddy bear  78 hair drier  79 toothbrush
```
