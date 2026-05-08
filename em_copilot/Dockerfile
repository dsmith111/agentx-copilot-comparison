FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY src /app/src
COPY pyproject.toml /app/pyproject.toml

ENV PYTHONPATH=/app/src \
    ADLS_LITE_HOST=0.0.0.0 \
    ADLS_LITE_PORT=10004 \
    ADLS_LITE_DATA_DIR=/data

VOLUME ["/data"]
EXPOSE 10004

HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:10004/health',timeout=2).read()==b'OK' else 1)" || exit 1

CMD ["python", "-m", "adls_lite.main"]
