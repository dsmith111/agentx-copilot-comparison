FROM python:3.12-slim

WORKDIR /app

# Install dependencies (pinned for reproducible builds)
COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi==0.136.1" "uvicorn[standard]==0.46.0"

# Copy source
COPY src/ ./src/

# Runtime config
ENV PYTHONPATH=/app/src
ENV ADLS_LITE_HOST=0.0.0.0
ENV ADLS_LITE_PORT=10004
ENV ADLS_LITE_MODE=snapshot
ENV ADLS_LITE_DATA_DIR=/var/lib/adls-lite

RUN mkdir -p /var/lib/adls-lite

EXPOSE 10004

HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10004/health')" || exit 1

CMD ["python3", "-m", "adls_lite"]
