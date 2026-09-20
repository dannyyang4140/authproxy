FROM python:3.12-slim

# 国内构建可传：--build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_INDEX_URL=""

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 非 root 运行，并准备数据目录
RUN useradd --uid 10001 --create-home appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

COPY requirements.txt ./
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip install -i "$PIP_INDEX_URL" -r requirements.txt; \
    else \
        pip install -r requirements.txt; \
    fi

COPY app ./app
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=6s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
