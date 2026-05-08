# syntax=docker/dockerfile:1.7
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EM_AGENTX_HOST=0.0.0.0 \
    EM_AGENTX_PORT=10004 \
    EM_AGENTX_DATA_DIR=/data

WORKDIR /app

# Install runtime deps first for better layer caching.
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.27"

COPY src ./src

RUN mkdir -p /data

EXPOSE 10004

HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=10 \
  CMD python -c "import urllib.request, sys; \
import os; port=os.environ.get('EM_AGENTX_PORT','10004'); \
import urllib.request as u; \
sys.exit(0 if u.urlopen(f'http://127.0.0.1:{port}/health', timeout=2).status==200 else 1)"

ENV PYTHONPATH=/app/src
CMD ["python", "-m", "em_agentx"]
