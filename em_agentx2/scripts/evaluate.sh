#!/usr/bin/env bash
set -euo pipefail

echo "[1/5] Running unit/API tests"
pytest -q

echo "[2/5] Building Docker image"
docker compose build

echo "[3/5] Starting emulator"
docker compose up -d

cleanup() {
  docker compose down -v || true
}
trap cleanup EXIT

echo "[4/5] Waiting for health endpoint"
for i in {1..60}; do
  if curl -fsS http://127.0.0.1:10004/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

curl -fsS http://127.0.0.1:10004/health

echo "[5/5] Running Azure SDK smoke test"
python3 examples/python_sdk_smoke.py

echo "PASS"