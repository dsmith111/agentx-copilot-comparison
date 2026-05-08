# ADLS Gen2 Lite Emulator

A lightweight, local emulator for Azure Data Lake Storage Gen2, built for development and testing
without requiring real Azure resources or an Azure login.

Compatible with `azure-storage-file-datalake` SDK (tested with 12.16.x and 12.23.x).

---

## Quick Start

### Docker (recommended)

```bash
docker compose up -d
```

The emulator listens on `http://127.0.0.1:10004`.

### Python SDK configuration

```python
from azure.storage.filedatalake import DataLakeServiceClient
from azure.core.credentials import AzureNamedKeyCredential

credential = AzureNamedKeyCredential(
    name="devstoreaccount1",
    key="Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==",
)

service = DataLakeServiceClient(
    account_url="http://127.0.0.1:10004/devstoreaccount1",
    credential=credential,
)
```

Use the standard Azurite / Storage Emulator shared key:
- **Account name:** `devstoreaccount1`
- **Account key:** `Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==`

### SDK installation

```bash
pip install azure-storage-file-datalake
```

---

## What is Emulated

| Feature | Status |
|---------|--------|
| Create / delete filesystem | implemented |
| List filesystems | implemented |
| Create directory | implemented |
| Create file (If-None-Match: *) | implemented |
| Append + flush (staged writes) | implemented |
| Read file (full + Range) | implemented |
| Head path (metadata) | implemented |
| List paths (recursive / non-recursive) | implemented |
| Rename (atomic, move) | implemented |
| Delete (file / directory, recursive) | implemented |
| Health endpoint (`GET /health`) | implemented |
| Persistence across restarts | implemented (Docker volume) |

Out of scope: ACLs, SAS tokens, shared access, properties metadata, leases, versioning.

---

## Acceptance Edge Cases

| EC | Description | Verified |
|----|-------------|---------|
| EC-1 | Repeated append/flush (multi-chunk write) | pass |
| EC-2 | Create file under existing file (conflict) | pass |
| EC-3 | If-None-Match: * prevents overwrite | pass |
| EC-4 | Read deleted file returns 404 | pass |
| EC-6 | Old path 404 after rename | pass |

---

## Running Tests

```bash
# Unit + API tests (no Docker needed)
pytest -q

# Full evaluation including Docker smoke test
./scripts/evaluate.sh
```

Expected without Docker: `60 passed, 3 skipped`.

---

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `ADLS_LITE_HOST` | `0.0.0.0` | Bind address |
| `ADLS_LITE_PORT` | `10004` | TCP port |
| `ADLS_LITE_ACCOUNT` | `devstoreaccount1` | Emulated account name |
| `ADLS_LITE_MODE` | `snapshot` | `memory` (no persistence) or `snapshot` (Docker volume) |
| `ADLS_LITE_DATA_DIR` | `/var/lib/adls-lite` | Data directory (snapshot mode) |
| `ADLS_LITE_LOG_LEVEL` | `info` | Uvicorn log level |

For tests, set `ADLS_LITE_MODE=memory`:

```bash
ADLS_LITE_MODE=memory python -m adls_lite
```

---

## Architecture

```
FastAPI app (src/adls_lite/app.py)
  |
  +-- catch-all route /{rest_path:path}
        |
        +-- _handle_filesystem()  PUT/DELETE/GET ?resource=filesystem|?restype=container
        +-- _handle_path()        PUT/PATCH/GET/HEAD/DELETE /<fs>/<path>
        +-- _handle_account()     GET /?comp=list

FilesystemStore (protocol)
  +-- InMemoryStore (tests, ADLS_LITE_MODE=memory)
  +-- SnapshotStore (Docker, ADLS_LITE_MODE=snapshot)
```

---

## SDK Compatibility Notes

SDK version 12.23.0+ routes `create_file_system` through the Blob container API
(`PUT ?restype=container`). The emulator handles both the DFS API
(`PUT ?resource=filesystem`) and the Blob API form transparently.

Use `AzureNamedKeyCredential` (from `azure.core.credentials`), not
`StorageSharedKeyCredential` (removed from `azure.storage.blob` in 12.23.0).

---

## Security Notes

This emulator is for **local development and testing only**. It does not enforce
authentication, authorization, or TLS. Do not expose port 10004 on a public network.
