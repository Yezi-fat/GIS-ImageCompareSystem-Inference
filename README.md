# GIS-ImageCompareSystem-Inference（map-change-python）

影像地图比对系统 —— Python 推理计算服务群（monorepo：3 微服务 + cv-common 共享库）。

| 服务 | 端口 | 职责 |
| --- | --- | --- |
| infer-service | 8001 | /infer/segmentation、/infer/change-detection；推理引擎（本地 GPU/CPU、远程）、Tile 管线、模型管理、环境能力上报 |
| compute-service | 8002 | /compute/diff、/compute/vectorize；差异三态计算、矢量化、Geod 面积、蒙版分色 |
| nlp-service | 8003 | /nlp/parse；NLU（LLM/规则降级）、地理编码适配、能力上报 |
| packages/cv-common | — | 共享库：契约 schema（唯一事实源）、CGCS2000 坐标工具、影像编解码、统一错误、鉴权与日志 |

## 开发环境

```bash
python3 -m venv .venv
.venv/bin/pip install -e packages/cv-common \
    -e services/infer-service -e services/compute-service -e services/nlp-service \
    pytest httpx
```

## 本地启动（三服务）

```bash
AUTH_ENABLED=false .venv/bin/uvicorn infer_service.main:app --port 8001
AUTH_ENABLED=false .venv/bin/uvicorn compute_service.main:app --port 8002
AUTH_ENABLED=false .venv/bin/uvicorn nlp_service.main:app --port 8003
```

或容器化一键起三服务：`docker compose up --build`（见 docker-compose.yml 注释的运行约束）。

## 测试与契约

```bash
.venv/bin/pytest packages/cv-common/tests services/*/tests
.venv/bin/python scripts/export_openapi.py   # 导出 docs/openapi/ 三份快照（接口变更后必跑，全局要求 #2）
```

## 模型与性能工具

```bash
.venv/bin/python scripts/make_fake_models.py        # 伪 ONNX 模型（联调主线，真实模型交付前）
.venv/bin/python scripts/quantize_int8.py <fp32.onnx>  # INT8 量化 + 精度比对报告（P-029）
# 性能基线：录制 benchmarks/baseline.json，CI 回归劣化 >20% 报警
.venv/bin/pytest services/infer-service/tests/test_benchmark.py --benchmark-only \
    --benchmark-json=benchmarks/baseline.json --benchmark-min-rounds=30
.venv/bin/python scripts/check_benchmark.py benchmarks/baseline.json <current.json>
```

## 文档

- 需求：`docs/影像变化检测后端推理服务需求分析文档.md`（V2.8）
- 详细设计：`docs/Python推理计算服务详细设计文档.md`（V2.6）
- 任务清单：`docs/Python服务开发任务清单.md`（V2.6）
- 评审闭环：`docs/Python侧设计评审与任务清单问题（已反馈）.md`
- 对 Java 侧接口需求：`docs/Python推理计算服务对Java侧服务接口需求文档.md`（V1.1）
- **Python 服务接口文档（Java 调用方用）**：`docs/Python推理计算服务接口文档.md`（V1.0，含实测示例）
- **部署文档（内网）**：`docs/Python推理计算服务部署文档.md`（V1.0，含 .env / compose / GPU 配置示例与验证清单）
- 模型交付流水线：`docs/模型交付流水线.md`（FP32/INT8 双产物、目录规范、注册切换）
- 对外契约快照：`docs/openapi/{infer,compute,nlp}.json`
- 现状盘点（2026-09-12）：`docs/Python侧功能欠缺与后续工作清单.md`、`docs/Java侧待确认与待修改事项.md`
- 联调往来：`docs/Java侧待确认与待修改事项-回复.md`（Java）、`docs/Python侧待办事项.md`（Java）→ `docs/Python侧待办事项-回复.md`（Python，全接受）
