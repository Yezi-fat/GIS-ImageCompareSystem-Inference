# compute-service 镜像（设计 §7.1）：无 onnxruntime/rasterio，镜像最小
# 内网离线 pip 源构建：docker build --build-arg PIP_INDEX_URL=http://<内网制品库>/simple
FROM python:3.11-slim

ARG PIP_INDEX_URL
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

COPY packages/cv-common /app/packages/cv-common
COPY services/compute-service /app/services/compute-service

# M3 起：opencv/shapely 已入主依赖（无 onnxruntime/rasterio，保持镜像最小，设计 §7.1）
RUN pip install --no-cache-dir \
    /app/packages/cv-common \
    /app/services/compute-service

EXPOSE 8002

CMD ["uvicorn", "compute_service.main:app", "--host", "0.0.0.0", "--port", "8002"]
