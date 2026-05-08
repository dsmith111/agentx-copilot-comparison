# Delivery Summary: ADLS Gen2 Lite Emulator

**Date:** 2026-05-06
**Status:** DELIVERED
**Go/No-Go Decision:** GO -- approved by Product Manager on 2026-05-06.

---

## What Was Built

A Dockerised ADLS Gen2 Lite Emulator that accepts the real
`azure-storage-file-datalake 12.23.0` Python SDK without any Azure credentials
or live Azure services.

### Key Features

- FastAPI/Uvicorn HTTP server on port 10004
- In-memory store (tests) + atomic snapshot store (Docker, persists to `/data/`)
- Full filesystem lifecycle: create, delete, list
- Full file lifecycle: create, append, flush, read (partial via x-ms-range), delete
- Directory create, list, recursive delete
- Rename: mode=rename/posix/legacy + x-ms-rename-source header
- Per-filesystem asyncio locks (concurrency contract)
- Docker image: Python 3.12-slim, zero external Azure dependencies
- Health endpoint: `GET /health`

---

## Acceptance Gate Results

| Gate | Result |
|------|--------|
| `pytest -q` (offline) | PASS -- 68 passed, 4 skipped |
| Docker image builds | PASS |
| Container health check returns 200 | PASS |
| `examples/python_sdk_smoke.py` (live SDK script) | PASS |
| `pytest -q tests/test_sdk_smoke.py` (live SDK pytest) | PASS -- 4 passed |
| `docker compose config -q` | PASS |
| Persistence across Docker restart | PASS -- `persistfs/persist.txt` read back as `persistent-data` after restart |
| `bash scripts/evaluate.sh` | PASS end to end |

---

## SDK Compatibility Fixes Delivered

| BUG | Description |
|-----|-------------|
| BUG-1 | `AzureNamedKeyCredential` replaces removed `StorageSharedKeyCredential` |
| BUG-2 | `?restype=container` routed to filesystem handlers |
| BUG-3 | Flush response returns metadata-only headers (empty body) |
| BUG-4 | Download uses `x-ms-range`, not `Range` header |
| BUG-5 | Rename uses `mode=legacy` + `x-ms-rename-source`; response has empty body |

---

## Code Review Findings Resolved

| ID | Severity | Resolution |
|----|----------|-----------|
| MEDIUM-1 | MEDIUM | SnapshotStore delete_filesystem blob file cleanup |
| MEDIUM-2 | MEDIUM | Per-filesystem lock applied to all mutating operations |
| LOW-1 | LOW | 416 with `bytes */0` on range request against empty file |
| MAJOR-1 (review 2026-05-06) | MAJOR | EC-3 wire-shape constraint documented in `_path_create_file`; supported overwrite pattern is `delete_file` -> `create_file` -> `append_data` -> `flush_data` |
| MAJOR-2 (review 2026-05-06) | MAJOR | Live SDK regression tests added: `test_create_file_duplicate_without_header_returns_409`, `test_sdk_upload_data_overwrite_not_supported` |

---

## Accepted Limitations (not blockers)

- `DataLakeFileClient.upload_data(overwrite=True)` on an existing file is intentionally rejected with 409 `PathAlreadyExists`. The SDK emits the same wire shape as a duplicate `create_file()`, and EC-3 requires duplicate create to fail. Documented in `src/adls_lite/app.py` and covered by `tests/test_sdk_smoke.py::test_sdk_upload_data_overwrite_not_supported`.

---

## Artifacts

| Artifact | Path |
|----------|------|
| Source | `src/adls_lite/` |
| Unit tests | `tests/` |
| SDK smoke | `examples/python_sdk_smoke.py`, `tests/test_sdk_smoke.py` |
| Docker | `Dockerfile`, `docker-compose.yml` |
| Evaluation | `scripts/evaluate.sh` |
| PRD | `docs/product/PRD-adls-gen2-lite-emulator.md` |
| ADR | `docs/architecture/ADR-adls-gen2-lite-emulator.md` |
| Spec | `docs/architecture/SPEC-adls-gen2-lite-emulator.md` |
| Test Plan | `docs/testing/TEST-PLAN-adls-gen2-lite-emulator.md` |
| Review | `docs/reviews/REVIEW-adls-gen2-lite-emulator.md` |
| DevOps Validation | `docs/devops/DEVOPS-VALIDATION-adls-gen2-lite-emulator.md` |
| Certification | `docs/testing/CERTIFICATION-adls-gen2-lite-emulator.md` |
| Learning | `docs/artifacts/learnings/LEARNING-adls-gen2-lite-emulator.md` |
