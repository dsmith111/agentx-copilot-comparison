# em_agentx — ADLS Gen2 Lite Emulator

> **Evaluation context:** This is the result of the **AgentX1** run — AgentX Auto used as a
> drop-in coding agent (no forced role separation). This run demonstrated that AgentX Auto
> alone does not clearly outperform a standard coding-agent workflow. See the
> [main README](../README.md) for results and comparisons.

---

A small, deterministic local emulator for a practical subset of Azure Data
Lake Storage Gen2. The real `azure-storage-file-datalake` Python SDK can be
pointed at this emulator to drive integration tests without touching Azure.

This is **not** a full Azure Storage replacement -- see `Design.md` and
"Known limitations" below.

## What works

Filesystem operations:
- create filesystem (DFS `?resource=filesystem` and Blob `?restype=container`)
- delete filesystem
- get filesystem properties

Path operations:
- create directory (auto-creates parent directories)
- create file (with overwrite)
- append + flush (positional, two-phase upload)
- read file (full and `x-ms-range` partial reads)
- HEAD (path properties)
- list paths (recursive and flat, with `directory=` prefix)
- rename file or directory (via `x-ms-rename-source` header)
- delete file or directory (with `recursive=true` for non-empty directories)

Runtime:
- FastAPI + uvicorn HTTP server on port `10004`
- `/health` endpoint
- in-memory mode for tests
- optional disk persistence via `EM_AGENTX_DATA_DIR`
- accepts any `Authorization` header (no SharedKey verification)
- Docker + docker-compose with persistent volume

## Layout

```
src/em_agentx/        # store + FastAPI app + CLI entrypoint
tests/                # unit, API, and SDK smoke tests
examples/python_sdk_smoke.py
scripts/evaluate.sh   # build + boot + SDK smoke gate
Dockerfile, docker-compose.yml
pyproject.toml
```

## Quick start

### Run with Docker (recommended)

```bash
docker compose up -d
curl http://127.0.0.1:10004/health   # -> OK
```

### Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m em_agentx          # listens on 0.0.0.0:10004
```

### Configuration

| Env var | Default | Purpose |
|---|---|---|
| `EM_AGENTX_HOST` | `0.0.0.0` | bind address |
| `EM_AGENTX_PORT` | `10004` | bind port |
| `EM_AGENTX_DATA_DIR` | (unset = in-memory) | enable disk persistence under this directory |
| `EM_AGENTX_ACCOUNT` | `devstoreaccount1` | account name accepted in URLs |
| `EM_AGENTX_LOG_LEVEL` | `info` | uvicorn log level |

## Using the Azure SDK

The emulator uses path-style URLs: `http://127.0.0.1:10004/<account>/<filesystem>/<path>`.

```python
from azure.storage.filedatalake import DataLakeServiceClient

# Any non-empty key is accepted; the well-known Azurite key is convenient.
KEY = ("Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
       "K1SZFPTOtr/KBHBeksoGMGw==")

svc = DataLakeServiceClient(
    account_url="http://127.0.0.1:10004/devstoreaccount1",
    credential=KEY,
)
fs = svc.create_file_system("demo")
fs.create_directory("dir1")

f = fs.get_file_client("dir1/hello.txt")
f.create_file()
data = b"hello world"
f.append_data(data, offset=0, length=len(data))
f.flush_data(len(data))

print(f.download_file().readall())   # -> b'hello world'
```

A complete script lives at `examples/python_sdk_smoke.py`.

## Tests

```bash
pip install -e ".[dev]"
pytest -q              # store unit tests + HTTP API tests + SDK in-process smoke
./scripts/evaluate.sh  # full Docker + SDK end-to-end gate
```

`scripts/evaluate.sh` builds the Docker image, starts the container, waits for
`/health`, then runs `examples/python_sdk_smoke.py` against the running
container.

## Known limitations

This emulator is intentionally a small subset for integration tests. It does
**not** implement:
- Azure Blob, Queue, Table, or Files APIs (only the DFS subset above plus the
  `restype=container` shape the DFS SDK uses internally for filesystem
  create/delete/get-properties)
- OAuth / Microsoft Entra ID authentication
- SharedKey signature verification (Authorization header is accepted but not
  validated)
- ACLs, leases, soft delete, encryption scopes, billing, account management
- Account-level filesystem listing (the SDK call is not exercised by the
  smoke test)
- Multipart parallel uploads beyond the create/append/flush pattern
- SAS token validation
- Exact Azure error XML body parity (errors are returned as JSON
  `{"error": {"code", "message"}}` with sensible HTTP status codes)
- Strict ordering guarantees for concurrent appends across multiple writers
  (single writer per file is supported; two-phase append/flush model is
  enforced)
- Delta Lake transaction protocol or any higher-level table semantics

## How it works

- `src/em_agentx/store.py` holds the in-memory data model (filesystems and
  paths). When `EM_AGENTX_DATA_DIR` is set, each filesystem is serialized to
  one JSON metadata file plus a directory of blob files keyed by a stable
  blob id; state is reloaded on startup.
- `src/em_agentx/app.py` is a FastAPI app with a single catch-all route that
  inspects method, path, query string, and selected `x-ms-*` headers to
  dispatch to the store. The catch-all design tolerates the SDK using both
  DFS-style (`?resource=filesystem|directory|file`) and Blob-style
  (`?restype=container`) requests against the same endpoint.
- The emulator strips an optional leading `/<account>` path segment so the
  same routes work for both `/<filesystem>/<path>` and
  `/<account>/<filesystem>/<path>` requests.

## License

Internal example project; no license granted.
