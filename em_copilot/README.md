# em_copilot — ADLS Gen2 Lite Emulator

> **Evaluation context:** This is the result of the **Copilot / Claude 4.7 1M** run — a standard
> coding-agent workflow with no structured role separation. It serves as both the initial setup
> form (shared with `em_base`) and the final delivered implementation. See the
> [main README](../README.md) for results and comparisons against the AgentX runs.

---

A small, Dockerized local emulator implementing a practical subset of the
[Azure Data Lake Storage Gen2](https://learn.microsoft.com/azure/storage/blobs/data-lake-storage-introduction)
filesystem/path REST surface. Aimed at integration tests where you want to
point the real `azure-storage-file-datalake` Python SDK at a local endpoint
without requiring an Azure subscription.

> Read [DESIGN.md](DESIGN.md) for the product contract. This README documents
> usage; DESIGN.md governs scope.

## Features

- Filesystem CRUD (create / list / delete)
- Directory and file create
- File `append` + `flush` (multi-part upload)
- File read (full and `Range`/`x-ms-range`)
- HEAD properties for files and directories
- List paths (flat and recursive, optional `directory=` prefix)
- Rename file or directory (atomic, including descendants)
- Delete file / directory (with `recursive=true|false`)
- Permissive auth: any `Authorization` header is accepted; no signature check
- Two storage modes: in-memory (tests) and JSON snapshot on disk (Docker volume)
- Tolerates URLs of either `/{account}/{filesystem}/...` or `/{filesystem}/...`

The Azure DataLake SDK delegates a few operations through its blob client, so
the emulator also accepts the blob-flavoured variants (`?restype=container`,
`?comp=list`).

## Quick start

### Run with Docker Compose

```bash
docker compose up --build -d
curl http://127.0.0.1:10004/health   # -> OK
```

The data lives on the named volume `adls_data` and survives container
restarts. To wipe it: `docker compose down -v`.

### Run locally without Docker

```bash
pip install -r requirements-dev.txt
PYTHONPATH=src python -m adls_lite.main          # in-memory by default
ADLS_LITE_DATA_DIR=./data PYTHONPATH=src python -m adls_lite.main  # persistent
```

Listens on `0.0.0.0:10004`.

### Run the SDK smoke test

With the emulator running:

```bash
python examples/python_sdk_smoke.py
```

The smoke test uses the *real* `azure-storage-file-datalake` SDK and exercises
the full create / append / flush / read / list / rename / delete cycle. It
does not contact Azure.

### Run the full evaluation

```bash
./scripts/evaluate.sh
```

This runs:

1. `pytest -q` (unit + API tests via `TestClient`)
2. `docker compose build`
3. `docker compose up -d`
4. Waits for `/health`
5. Runs `examples/python_sdk_smoke.py` against the running container
6. Tears the container down

## Configuring the Azure SDK

```python
from azure.storage.filedatalake import DataLakeServiceClient

# Azurite-compatible well-known account key. The emulator does not validate
# signatures; any non-empty key works.
ACCOUNT_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="

service = DataLakeServiceClient(
    account_url="http://127.0.0.1:10004/devstoreaccount1",
    credential=ACCOUNT_KEY,
)

fs = service.create_file_system("demo")
fs.create_directory("uploads")
file_client = fs.get_file_client("uploads/hello.txt")
file_client.create_file()
data = b"hello world"
file_client.append_data(data, offset=0, length=len(data))
file_client.flush_data(len(data))
print(file_client.download_file().readall())
```

The path-style URL (`http://host:port/{accountname}`) is the local-emulator
form recognised by the Storage SDKs (this is how Azurite is targeted).

## Configuration

Environment variables consumed by `python -m adls_lite.main`:

| Variable               | Default            | Purpose                              |
|------------------------|--------------------|--------------------------------------|
| `ADLS_LITE_HOST`       | `0.0.0.0`          | Bind address                         |
| `ADLS_LITE_PORT`       | `10004`            | Listen port                          |
| `ADLS_LITE_ACCOUNT`    | `devstoreaccount1` | Account name surface in URLs         |
| `ADLS_LITE_DATA_DIR`   | (unset = in-mem)   | If set, snapshot state to this dir   |

## Endpoints (subset)

| Method | Path                                                    | Notes                                  |
|--------|---------------------------------------------------------|----------------------------------------|
| GET    | `/health`                                               | Liveness probe -- returns `OK`         |
| PUT    | `/{fs}?resource=filesystem` or `?restype=container`     | Create filesystem                      |
| DELETE | `/{fs}` (or `?restype=container`)                       | Delete filesystem                      |
| HEAD   | `/{fs}`                                                 | Filesystem exists                      |
| GET    | `/{fs}?resource=filesystem&recursive=…&directory=…`     | List paths (JSON `{"paths":[…]}`)      |
| PUT    | `/{fs}/{path}?resource=directory`                       | Create directory                       |
| PUT    | `/{fs}/{path}?resource=file`                            | Create file                            |
| PATCH  | `/{fs}/{path}?action=append&position=N`                 | Append bytes at `N`                    |
| PATCH  | `/{fs}/{path}?action=flush&position=N`                  | Truncate-flush to `N`                  |
| GET    | `/{fs}/{path}` (optional `Range` / `x-ms-range`)        | Read file (always emits `Content-Range`)|
| HEAD   | `/{fs}/{path}`                                          | File / directory properties            |
| PUT    | `/{fs}/{path}?mode=legacy` + `x-ms-rename-source`       | Rename                                 |
| DELETE | `/{fs}/{path}?recursive=true|false`                     | Delete                                 |

URLs may be prefixed with the account name (`/{account}/...`) -- the SDK does
this -- or omit it.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # unit + API tests
PYTHONPATH=src python -m adls_lite.main   # run the server
```

Project layout:

```
src/adls_lite/
  app.py        # FastAPI app + ASGI middleware + dispatch
  store.py      # In-memory hierarchical-namespace store + JSON persistence
  main.py       # uvicorn entrypoint
tests/
  test_store.py # Unit tests for the store
  test_api.py   # HTTP API tests via Starlette TestClient
examples/
  python_sdk_smoke.py
scripts/
  evaluate.sh   # End-to-end gate (pytest + docker + SDK smoke test)
```

## Known limitations (intentional MVP scope)

These are deliberate omissions documented in `DESIGN.md` -- the goal is
"smallest correct subset for SDK integration tests", not API parity:

- No SharedKey / SAS / Entra ID signature validation; any auth header is
  accepted.
- No ACLs, no leases, no soft delete, no encryption scopes, no billing.
- No Blob, Queue, Table, or Files API surface; only the ADLS Gen2
  filesystem/path subset that the Python SDK exercises.
- `DataLakeServiceClient.list_file_systems` returns minimal XML/JSON --
  enough for enumeration but missing many properties.
- Persistence is a single JSON snapshot file rewritten on every mutation;
  fine for test workloads, not for large data.
- No real Azure error XML body; errors are JSON `{ "error": { "code", "message" } }`.
- Concurrency is single-process with a `RLock` -- not designed for
  high-concurrency benchmarking.
- No HTTPS; bind a reverse proxy (or Azurite-style cert) if you need it.

## License

MIT (or whichever license the surrounding repository uses).
