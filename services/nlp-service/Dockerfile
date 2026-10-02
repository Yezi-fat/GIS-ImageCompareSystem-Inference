# nlp-service 镜像（设计 §7.1）：jieba + httpx，镜像最小
# 内网离线 pip 源构建：docker build --build-arg PIP_INDEX_URL=http://<内网制品库>/simple
FROM python:3.11-slim

ARG PIP_INDEX_URL
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

COPY packages/cv-common /app/packages/cv-common
COPY services/nlp-service /app/services/nlp-service

# M4 起：jieba 已入主依赖（instructor 未采用，见 pyproject 注记）
RUN pip install --no-cache-dir \
    /app/packages/cv-common \
    /app/services/nlp-service

EXPOSE 8003

CMD ["uvicorn", "nlp_service.main:app", "--host", "0.0.0.0", "--port", "8003"]
