#!/usr/bin/env bash
set -euo pipefail

# Resolve a usable Python interpreter; CI images may only ship one of them.
if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  echo "No python interpreter found on PATH" >&2
  exit 1
fi

echo "[1/5] Running unit/API tests"
"$PY" -m pytest -q

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
"$PY" examples/python_sdk_smoke.py

echo "PASS"