# 部署实例说明（2026-09-21，yolo11 v1.0 真实模型接入）

本目录为一次完整部署的全部文件（配置、编排、测试数据），按
《Python推理计算服务部署文档》V2.0 执行。通用运维流程以该文档为准，
本文件只记录**本次部署的实例差异与实测结果**。

## 1. 文件清单

| 文件 | 用途 |
| --- | --- |
| `.env` | 本次部署配置（模板为 `.env.example`；`INTERNAL_TOKEN=dev-internal-token` 与 Java 侧同值——曾误用本地生成令牌导致 Java 调用 401、前端误判"登录过期"，已修正） |
| `docker-compose.yml` | 本次部署编排（在本目录内 `docker compose up -d` 即可，项目目录自动锚定本目录） |
| `testdata/bus.jpg` | 验证用 COCO 实景图（val2017，含 bus/person/traffic light） |
| `.env.example` / `docker-compose.prod.yml` | 通用模板（生产 `/data/models` 形态），本次部署未直接使用 |

## 2. 本次部署形态

- **识别（/infer/segmentation）**：真实模型 **yolo11 v1.0**（COCO 80 类检测），
  宿主机 `models/yolo11/v1.0/{fp32,int8}.onnx`，经 `../models:/models:ro` 挂载进容器；
- **比对**：segmentation×2 + `/compute/diff` 路径（FR-6→FR-7 编排由 Java 侧发起）；
- **端到端变化检测（/infer/change-detection）**：仍挂 **v0.0-fake 伪模型**——
  yolo11 为单输入检测模型，无法承担双输入变化检测；真实变化检测模型待模型团队交付
  （登记为既有遗留事项 P-014 变化检测支线）；
- **nlp-service**：未配置 LLM/地理编码 → 常驻规则解析降级形态（FR-9.6/9.8，正常形态）；
- 提供方：local-cpu（本机无 GPU），int8 量化版。

## 3. 关键决策与偏差记录

### 3.1 yolo11 检测输出适配（P-014 收口，代码变更）

yolo11 输出为检测框 `[1, 84, anchors]`（非逐像素概率图），新增
`engines/yolo_decode.py` 栅格化为 `[81, H, W]` 概率图：
通道 0~79 = COCO 类别序号**直用**，通道 80（末位）= 背景残差。
`engines/local_onnx.py` segment() 按输出维度自动分发两种格式（伪模型链路不受影响，
126 测试全过）。

**对 Java 侧的契约约定（重要）**：本部署下 `class_mapping` 的类别值 =
**COCO 类别序号直用（0~79）**；**80 保留为背景，禁止映射**。
`MODELS__SEGMENTATION__CLASS_IDS=[0..80]`。
置信度/NMS 阈值经 `MODELS__SEGMENTATION__CONF_THRESHOLD=0.25` /
`MODELS__SEGMENTATION__NMS_THRESHOLD=0.45` 配置。

> ⚠️ 语义提醒：yolo11 经元数据验证为 **COCO 预训练通用检测模型**（人/车等 80 类），
> 不含森林/草地/雪山/建筑等地物类别。可用于全链路真实推理测试；
> 地物业务识别须模型团队交付地物训练的模型（届时按部署文档 §8 放置并通知 Java 切换）。
> Java 侧接入修改事项见《docs/Java侧yolo11模型接入修改事项.md》。

### 3.2 交付 int8 产物失效，已重制

交付包 `int8.onnx`（静态量化，59MB）实测输出全零（maxconf=0.000），不可用；
已备份为 `models/yolo11/v1.0/int8.delivered-broken.onnx`，并用仓库
`scripts/quantize_int8.py`（动态量化）重新生成：58.2MB，真实图片检测与 fp32 一致
（bus×10/person×2~3/traffic light×9~11，maxconf 0.967 vs fp32 0.961）。
量化脚本张量级报告 rel_err=7.5%（随机噪声输入下的固有口径偏差，不代表检测精度）；
真实图片对比一致，可用。**建议反馈模型团队：静态量化流水线需复核。**

### 3.3 预处理收口

loader 已统一 RGB（OpenCV BGR→RGB），0~1 归一化与 YOLO 训练口径一致，
`preprocess.py` 的 P-014 TODO 已核销（无需改动逻辑）。

## 4. 部署与验证命令

```bash
cd deploy
docker compose up -d

# 健康检查（/health 免鉴权）
curl -s http://localhost:8001/health
curl -s http://localhost:8002/health
curl -s http://localhost:8003/health

# 鉴权检查（应 401）
curl -s -X POST http://localhost:8001/infer/segmentation \
  -H 'Content-Type: application/json' -d '{}'
```

真实推理验证（容器网络内供图，token 见 .env）：

```bash
# 启动临时供图容器（compute 镜像内 python 兼任）
docker run -d --name imgsrv --network mapchange_default \
  -v "$PWD/testdata:/data:ro" --entrypoint python \
  gis-imagecomparesystem-inference-compute-service:latest \
  -m http.server 8000 --directory /data

# 识别：bus（COCO 类别序号 5 直用）
curl -s -X POST http://localhost:8001/infer/segmentation \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H 'Content-Type: application/json' -d '{
    "image_url": "http://imgsrv:8000/bus.jpg",
    "geo_extent": [104.01, 30.69, 104.07, 30.73],
    "elements": ["bus"],
    "class_mapping": {"bus": 5},
    "colors": {"bus": "#FF0000"},
    "min_area": 100
  }'

# 比对：两期蒙版差异（用识别结果蒙版，或合成位移用例）
curl -s -X POST http://localhost:8002/compute/diff \
  -H "X-Internal-Token: $INTERNAL_TOKEN" -H 'Content-Type: application/json' -d '{...}'

# 验证完清理：docker rm -f imgsrv
```

## 5. 实测结果（2026-09-21）

三容器 Up（python-infer/compute/nlp-service，镜像为含 yolo_decode 适配的重建版）：

| # | 验证项 | 结果 |
| --- | --- | --- |
| 1 | 三服务 /health | 均 `status=ok`；infer 上报 `cpu_cores=32, gpu_available=false`（本机无 GPU，如实） |
| 2 | 鉴权 | 无 token POST /infer/segmentation → **401**；带 token → 通过 |
| 3 | 模型挂载 | `docker exec python-infer-service ls /models/yolo11/v1.0` → fp32.onnx + int8.onnx |
| 4 | **真实模型识别** | bus.jpg（640×427），elements=[bus,person]，class_mapping bus→6/person→1 → **bus area_px=116272、patch_count=1、area_m2≈1084 万**，model_info={yolo11, v1.0, local-cpu}，耗时 1213ms；person 检出但被 min_area=100 过滤（小目标，符合预期） |
| 5 | **比对（compute/diff）** | before=空白 / after=bus 蒙版 → added_px=114817（=识别面积减 1px 边缘腐蚀）、removed=0、change_rate=1.0（从无到有），双单位统计正确 |
| 6 | 变化检测（伪模型） | 同尺寸双期 → change_count=6、total_area_px=3065，21ms；尺寸不一致双期 → 正确返回 **GEO_EXTENT_MISMATCH**（契约行为） |
| 7 | nlp/parse（降级形态） | "识别四川成都金牛区xx街道的建筑群" → intent=feature_extraction、elements=[building]、词典 bbox、nlu_provider=rule-based |
| 8 | 推理后 /health | `models.segmentation.loaded=true, version=v1.0, provider=local-cpu`（懒加载生效） |

验证完毕已清理临时供图容器（`docker rm -f imgsrv`）；三业务容器保持运行。
