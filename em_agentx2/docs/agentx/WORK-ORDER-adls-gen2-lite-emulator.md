# Work Order: ADLS Gen2 Lite Emulator

## Goal

Build a Dockerized local emulator for a practical subset of Azure Data Lake Storage Gen2 that can be driven by the real `azure-storage-file-datalake` Python SDK.

## Acceptance criteria

- `docker compose up` starts emulator on port `10004`
- `/health` returns `OK`
- `pytest -q` passes
- `scripts/evaluate.sh` passes
- `examples/python_sdk_smoke.py` uses the real Azure SDK against the local emulator
- no live Azure resource is used

## Required AgentX workflow artifacts

- `docs/product/PRD-adls-gen2-lite-emulator.md`
- `docs/research/ADLS-GEN2-SDK-COMPATIBILITY-NOTES.md`
- `docs/architecture/ADR-adls-gen2-lite-emulator.md`
- `docs/architecture/SPEC-adls-gen2-lite-emulator.md`
- `docs/testing/TEST-PLAN-adls-gen2-lite-emulator.md`
- `docs/reviews/REVIEW-adls-gen2-lite-emulator.md`
- `docs/devops/DEVOPS-VALIDATION-adls-gen2-lite-emulator.md`
- `docs/testing/CERTIFICATION-adls-gen2-lite-emulator.md`
- `docs/execution/DELIVERY-SUMMARY-adls-gen2-lite-emulator.md`
- `docs/artifacts/learnings/LEARNING-adls-gen2-lite-emulator.md`

## Source of truth

Use these files as the product contract:

- `DESIGN.md`
- `AGENTS.md`
- `scripts/evaluate.sh`

Do not weaken the acceptance criteria.
Do not use live Azure resources.
Do not remove tests to make the project pass.

## Scope

Implement a local ADLS Gen2-compatible subset, not a full Azure Storage replacement.

Required SDK lifecycle:

1. create filesystem
2. create directory
3. create file
4. append bytes
5. flush bytes
6. download/read file
7. list paths
8. rename file
9. delete file
10. delete filesystem

## Hidden edge cases to cover

- repeated append/flush
- child path under file parent must fail
- `If-None-Match: *` on existing file must fail
- deleted file should raise `ResourceNotFoundError` through SDK
- persistence survives Docker restart
- old path missing after rename
