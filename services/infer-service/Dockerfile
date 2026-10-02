# infer-service CPU 版镜像（设计 §7.1）
# 内网离线 pip 源构建：docker build --build-arg PIP_INDEX_URL=http://<内网制品库>/simple
FROM python:3.11-slim

ARG PIP_INDEX_URL
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

# cv-common 共享库（可安装包，schema 即契约）
COPY packages/cv-common /app/packages/cv-common
COPY services/infer-service /app/services/infer-service

# M2 起：opencv/rasterio/onnxruntime 已入主依赖（GPU 变体走 Dockerfile.gpu）
RUN pip install --no-cache-dir \
    /app/packages/cv-common \
    /app/services/infer-service

EXPOSE 8001
# 模型目录以 volume 挂载（/models/{name}/{version}/{fp32|int8}.onnx，评审 P-02/J-05）
VOLUME ["/models"]

CMD ["uvicorn", "infer_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
