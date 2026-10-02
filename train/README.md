# YOLO11 影像地图要素检测模型训练流水线

针对本系统业务要素（**森林 forest / 草地 grassland / 雪山覆盖 snow / 建筑 building**）
训练 YOLO11 检测模型，并导出为推理服务可直接部署的 ONNX 双产物。

| 环节 | 脚本 | 说明 |
| --- | --- | --- |
| ① 数据转换 | `mask_to_yolo.py` | 语义分割公开数据集（掩膜 PNG）→ YOLO 检测标注 |
| ② 训练 | `train.py` + `data.yaml` | Ultralytics YOLO11 训练/验证 |
| ③ 导出部署 | `export_onnx.py` | 导出动态尺寸 fp32.onnx + 调用仓库量化脚本产 int8.onnx，按 `{name}/{version}/` 落位 |
| 环境 | `requirements.txt` | 建议装入已有 conda env `yolo11` |

## 0. 类别约定（与系统契约对齐，勿随意改）

`data.yaml` 类别顺序固定为：

```
0: forest（森林）  1: grassland（草地）  2: snow（雪山覆盖）  3: building（建筑）
```

推理侧 `yolo_decode` 约定：**class_mapping 值 = 类别序号直用**，背景通道 = 类别数（此处为 4）。
因此部署后 Java 要素目录映射为 `forest→0, grassland→1, snow→2, building→3`，
Python 配置 `MODELS__SEGMENTATION__CLASS_IDS=[0,1,2,3,4]`（4=背景，禁映射）。

## 1. 环境准备

```bash
conda activate yolo11          # 已有环境；或 conda create -n yolo11 python=3.11
pip install -r train/requirements.txt
```

> ⚠️ **训练需要 GPU**（本机 WSL2 无 GPU，YOLO11s 100 epochs 在 CPU 上不可行）。
> 把 `train/`、数据集与仓库 `scripts/quantize_int8.py` 拷到 GPU 机器执行；
> 导出产物再拷回本仓库 `models/` 部署。

## 2. 数据准备（最关键，决定模型上限）

### 2.1 数据来源建议

| 数据集 | 内容 | 可覆盖类别 | 许可 |
| --- | --- | --- | --- |
| **LoveDA** | 0.3m 遥感语义分割，5987 图，7 类 | forest、grassland(≈agriculture)、building | 学术免费 |
| **LandCover.ai** | 0.25~0.5m 航拍语义分割，41 图幅大图 | forest(≈woodlands)、building | CC BY-NC-SA |
| **DeepGlobe Land Cover** | 0.5m，803 图，6 类 | forest、grassland(≈agriculture/rangeland) | 学术免费 |
| **自标注雪山区** | 雪山类别公开检测数据稀缺，建议用业务区影像经 CVAT/labelme 标注数百张 | snow | — |

**领域一致性忠告**：模型推理对象是**瓦片地图影像**（渲染后的 RGB 瓦片），
用与业务瓦片同源/同风格的影像训练效果远好于纯原始遥感影像；
条件允许时，直接从业务底图截瓦片自标注是最优数据。

### 2.2 掩膜 → YOLO 标注转换（语义分割数据集适用）

LoveDA/LandCover.ai 等给的是逐像素掩膜 PNG，本脚本转 YOLO 检测标注
（每类取连通区域外接框，过滤小斑块）：

```bash
python train/mask_to_yolo.py \
  --images /data/loveda/images \
  --masks  /data/loveda/masks \
  --mask-mapping '{"1":0,"2":1,"3":2,"4":3}'   # 掩膜像素值→本模型类别（按数据集实际调色板改）
  --out train/dataset --val-ratio 0.1 --min-area-px 64
```

产物：

```
train/dataset/
├── images/{train,val}/*.png|jpg
└── labels/{train,val}/*.txt     # YOLO 格式：<cls> <cx> <cy> <w> <h>（归一化）
```

**已是 YOLO 标注的数据**（自标注导出）：直接按上述目录结构摆放，跳过转换。

### 2.3 标注规范（自标注时遵守）

- 区域型要素（森林/雪山）按**连通区块**标一个外接框，不要把整片区域切碎，也不要一框包半张图（>60% 图幅的框拆成若干子区域）；
- 建筑群按**团块**标注（一片建成区一框），不必精确到单体；
- 每类训练样本建议 ≥1000 个标注实例，雪山上限不足时以数据增广弥补。

## 3. 训练

```bash
# 基线（推荐 yolo11s：精度/体积平衡，CPU INT8 推理友好；x 版 228MB 不适合本系统）
python train/train.py --data train/data.yaml --weights yolo11s.pt \
  --epochs 100 --imgsz 256 --batch 16 --device 0

# 快速冒烟（验证管线，几分钟）
python train/train.py --data train/data.yaml --weights yolo11n.pt --epochs 3 --imgsz 256 --batch 8
```

- `--imgsz 256` 与推理侧 Tile 尺寸（256×256，重叠 32）**保持一致**——训练/推理同尺度，避免尺度失配掉点；
- 训练产物在 `train/runs/<name>/weights/{best,last}.pt`；
- 看指标：`mAP50-95` 之外重点看**每类 precision/recall**——雪山样本少时该类会明显偏低，据此补数据。

## 4. 导出与部署接入

```bash
# 在仓库根目录执行（导出动态 H/W fp32 + 量化 int8，落位 models/ 规范结构）
python train/export_onnx.py --weights train/runs/landcover-yolo11s/weights/best.pt \
  --name landcover-seg --version v3.0
```

脚本自动完成：
1. 导出 `fp32.onnx`（**动态 H/W**，输出 `[1, 4+4, anchors]`，与 `yolo_decode` 契约匹配）；
2. 调用仓库 `scripts/quantize_int8.py` 产 `int8.onnx`（含精度比对报告，≤2% 口径）；
3. 按 `models/landcover-seg/v3.0/{fp32,int8}.onnx` 落位；
4. 打印后续配置指引。

部署切换（详见《Python推理计算服务部署文档》§8 与《Java侧yolo11模型接入修改事项.md》）：

```bash
# ① deploy/.env（重启 infer 生效：cd deploy && docker compose up -d --force-recreate infer-service）
MODELS__SEGMENTATION__NAME=landcover-seg
MODELS__SEGMENTATION__VERSION=v3.0
MODELS__SEGMENTATION__CLASS_IDS=[0,1,2,3,4]      # 4 类 + 背景通道 4

# ② Java 侧：激活模型（PUT /api/v1/models/active，热生效无需重启）
#    并将要素目录 model_class_id 改为 forest→0, grassland→1, snow→2, building→3
```

## 5. 验收清单

| # | 检查 | 口径 |
| --- | --- | --- |
| 1 | 量化报告 | `quantize_int8.py` 退出码 0（张量级 ≤2%）；超标时在业务测试集复测 mAP 对比 |
| 2 | 容器内冒烟 | 经 imgsrv 供图调 /infer/segmentation，各类别蒙版/统计合理（命令见 deploy/README.md §4） |
| 3 | 业务图抽查 | ≥20 张真实瓦片人工核对蒙版覆盖，森林/建筑为重点 |
| 4 | 性能 | 256² 单 Tile CPU 耗时记录进 `benchmarks/baseline.json`（真实模型基线重录，P-026 遗留项） |
