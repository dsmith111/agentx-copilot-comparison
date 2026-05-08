# DevOps Validation: ADLS Gen2 Lite Emulator

**Date:** 2026-05-06
**Validated by:** AgentX Tester (automated)

---

## Docker Build

```
docker build -t adls-lite-emulator .
```

Expected result: image built with Python 3.12-slim, PYTHONPATH=/app/src, uvicorn on port 10004.

## Health Check

```
curl -s -o /dev/null -w "%{http_code}" http://localhost:10004/health
```

Expected: `200`

## Compose Validation

```
docker compose config -q
```

Expected result: compose file validates with no output.

## Persistence Restart Test

1. Start: `docker run -v $PWD/data:/data -p 10004:10004 adls-lite-emulator`
2. Create filesystem + upload file via SDK
3. Stop container
4. Restart same command
5. Read file via SDK -- data must match

SnapshotStore persists state to `/data/v1/snapshot.json` + `/data/v1/blobs/*.bin`. Atomic
`tempfile -> os.replace` prevents partial-write corruption.

Validated on 2026-05-06 with the compose-managed named volume:

- seeded `persistfs/persist.txt` with `persistent-data` through the real SDK
- restarted the emulator with `docker compose down` -> `docker compose up -d` (volume preserved)
- read back the same file through the real SDK after restart
- observed result: `persistent-data`

## evaluate.sh

```
./scripts/evaluate.sh
```

Current end-to-end validation pass:

| Step | Description | Result |
|------|-------------|--------|
| 1 | `pytest -q` (unit + API tests, no Docker) | PASS -- 68 passed, 4 skipped |
| 2 | Docker image builds | PASS |
| 3 | Container starts + /health responds 200 | PASS |
| 4 | SDK smoke test script (`examples/python_sdk_smoke.py`) | PASS |
| 5 | Live SDK pytest suite (`python3 -m pytest -q tests/test_sdk_smoke.py`) | PASS -- 4 passed |
| 6 | Compose configuration (`docker compose config -q`) | PASS |
| 7 | Persistence survives Docker restart | PASS -- `persistfs/persist.txt` read back as `persistent-data` after restart |

## Known Limitations

- Single-process; no clustering; no TLS.
- Not a production replacement for Azure ADLS Gen2 -- emulator only.
- Offline `pytest -q` skips the SDK smoke tests when the emulator is not running on port 10004.
- `upload_data(overwrite=True)` on an existing file is intentionally unsupported because the SDK emits the same wire request shape as a second `create_file()` call, and EC-3 requires duplicate create to return 409.
