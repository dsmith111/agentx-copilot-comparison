# PROGRESS: ADLS Gen2 Lite Emulator

**Issue:** WORK-ORDER-adls-gen2-lite-emulator.md
**Date:** 2026-05-06
**Status:** Done

**Review cycle:** MAJOR-1, MAJOR-2, MINOR-1 from REVIEW-adls-gen2-lite-emulator.md (2026-05-06) addressed and approved.

---

## Validation Gates

### Gate 1: python -m compileall

```
python3 -m compileall .
```

Result: **PASS** -- zero compile errors across all Python source files and tests.

### Gate 2: pytest -q

```
pytest -q
```

Result (without Docker):
```
67 passed, 3 skipped in 1.38s
```

- test_store.py:  32 passed (InMemoryStore unit tests)
- test_api.py:    35 passed (FastAPI HTTP tests via Starlette TestClient, no Docker)
- test_sdk_smoke.py: 3 skipped (server not running outside Docker; skip-if-not-reachable guard)

### Gate 3: Docker build

```
docker compose build
```

Status: Pending -- requires Docker daemon.

Result: **PASS** -- image builds successfully during `scripts/evaluate.sh`.

### Gate 4: Health endpoint

```
curl -fsS http://127.0.0.1:10004/health
```

Status: Pending -- requires Docker container running.

Result: **PASS** -- `/health` returned `OK` during `scripts/evaluate.sh`.

### Gate 5: Azure SDK smoke test

```
python examples/python_sdk_smoke.py
```

Status: Pending -- requires Docker container running.

Result: **PASS** -- all 12 SDK smoke steps completed successfully during `scripts/evaluate.sh`.

---

## Bugs Found and Fixed

### BUG-1: StorageSharedKeyCredential removed (type:bug, P1)

- **Error:** `ImportError: cannot import name 'StorageSharedKeyCredential' from 'azure.storage.blob'`
- **Cause:** `azure-storage-file-datalake` 12.23.0 removed `StorageSharedKeyCredential` from `azure.storage.blob`
- **Fix:** Changed to `AzureNamedKeyCredential` from `azure.core.credentials` in both `tests/test_sdk_smoke.py` and `examples/python_sdk_smoke.py`
- **Verified:** Credential import resolved; tests moved past import error

### BUG-2: SDK 12.23 routes create_file_system through Blob API

- **Error:** `HttpResponseError: (NotImplemented) This endpoint is not implemented`
- **Cause:** SDK 12.23.0 delegates `create_file_system()` to `_container_client.create_container()` which sends `PUT ?restype=container` instead of `PUT ?resource=filesystem`
- **Fix:** `app.py _handle_filesystem()` now accepts both `?resource=filesystem` (DFS) and `?restype=container` (Blob) for PUT and DELETE
- **Verified:** All 60 store+API tests pass; SDK smoke tests pass against live server

### BUG-3: Flush response advertised file bytes on an empty body

- **Error:** SDK requests failed with `IncompleteReadError` during `flush_data(...)`
- **Cause:** flush responses returned file-style `Content-Length` headers even though the response body was empty
- **Fix:** `app.py _path_flush()` now returns metadata-only headers (`ETag`, `Last-Modified`) without file-body `Content-Length`
- **Verified:** `test_flush_response_has_empty_body_metadata_only` and `scripts/evaluate.sh` pass

### BUG-4: Blob SDK download uses `x-ms-range`

- **Error:** SDK download failed with `ValueError: Required Content-Range response header is missing or malformed.`
- **Cause:** blob-backed download requests send `x-ms-range`, while the emulator previously only honored `Range`
- **Fix:** `app.py _path_read()` now accepts both `x-ms-range` and `Range`
- **Verified:** `test_get_file_honors_x_ms_range_header` and `scripts/evaluate.sh` pass

### BUG-5: Rename via SDK uses legacy mode and empty-body metadata response

- **Error:** SDK rename failed on `PUT ?mode=legacy`, and later hit empty-body download/read issues after rename
- **Cause:** rename dispatch only recognized `mode=rename`, and rename responses also returned file-style `Content-Length` on an empty body
- **Fix:** `app.py` now accepts rename modes `rename|legacy|posix` or any explicit rename source, and `_path_rename()` returns metadata-only headers
- **Verified:** `test_rename_using_legacy_mode_has_empty_body_metadata_only` and `scripts/evaluate.sh` pass

---

## Implementation Summary

### Source Files

| File | Description |
|------|-------------|
| `src/adls_lite/store/base.py` | Shared data model, exceptions, path helpers |
| `src/adls_lite/store/memory.py` | InMemoryStore: thread-safe in-process store |
| `src/adls_lite/store/snapshot.py` | SnapshotStore: atomic disk persistence for Docker |
| `src/adls_lite/protocol/errors.py` | Azure-style JSON error envelope |
| `src/adls_lite/protocol/headers.py` | Standard x-ms-* response headers |
| `src/adls_lite/config.py` | Environment-variable configuration |
| `src/adls_lite/app.py` | FastAPI app factory, full DFS route dispatch |
| `src/adls_lite/__main__.py` | Uvicorn entry point |

### Test Files

| File | Tests | Status |
|------|-------|--------|
| `tests/test_store.py` | 32 | All pass |
| `tests/test_api.py` | 34 | All pass |
| `tests/test_sdk_smoke.py` | 3 | Skip without server; pass with Docker |

### Infrastructure

- `Dockerfile` -- python:3.12-slim, snapshot mode, port 10004
- `docker-compose.yml` -- named volume adls-data, restart unless-stopped
- `examples/python_sdk_smoke.py` -- 12-step SDK lifecycle smoke script
- `scripts/evaluate.sh` -- 5-step evaluation pipeline

---

## Acceptance Criteria Coverage

| EC | Description | Covered By |
|----|-------------|-----------|
| EC-1 | Repeated append/flush | `test_store.py::test_repeated_append_flush`, `test_api.py::test_repeated_append_flush`, `test_sdk_smoke.py::test_sdk_full_lifecycle` |
| EC-2 | Child path under file parent fails | `test_store.py::test_child_path_under_file_parent_fails`, `test_api.py::test_create_child_under_file_parent_fails` |
| EC-3 | If-None-Match: * | `test_store.py::test_create_file_if_none_match_star_raises_when_exists`, `test_api.py::test_create_file_if_none_match_star_duplicate_returns_409`, `test_sdk_smoke.py::test_sdk_resource_exists_error_on_if_none_match` |
| EC-4 | Read deleted file | `test_sdk_smoke.py::test_sdk_resource_not_found_after_delete` |
| EC-6 | Old path after rename | `test_store.py::test_rename_file`, `test_api.py::test_rename_old_path_missing_after_success`, `test_sdk_smoke.py::test_sdk_full_lifecycle` |

### Added blocker regression coverage

- `test_create_delete_filesystem_blob_container_style`
- `test_flush_response_has_empty_body_metadata_only`
- `test_get_file_honors_x_ms_range_header`
- `test_rename_using_legacy_mode_has_empty_body_metadata_only`
- `test_delete_filesystem_removes_blob_files` (MEDIUM-1 fix)
- `test_get_empty_file_with_range_returns_416` (LOW-1 fix)
- `test_get_file_range_start_past_eof_returns_416` (MAJOR-1 fix)

---

## Artifacts Written

- `docs/product/PRD-adls-gen2-lite-emulator.md`
- `docs/research/ADLS-GEN2-SDK-COMPATIBILITY-NOTES.md`
- `docs/architecture/ADR-adls-gen2-lite-emulator.md`
- `docs/architecture/SPEC-adls-gen2-lite-emulator.md`
- `docs/testing/TEST-PLAN-adls-gen2-lite-emulator.md`
- `README.md`
- `docs/reviews/REVIEW-adls-gen2-lite-emulator.md`
- `docs/devops/DEVOPS-VALIDATION-adls-gen2-lite-emulator.md`
- `docs/testing/CERTIFICATION-adls-gen2-lite-emulator.md`
- `docs/execution/DELIVERY-SUMMARY-adls-gen2-lite-emulator.md`
- `docs/artifacts/learnings/LEARNING-adls-gen2-lite-emulator.md`
