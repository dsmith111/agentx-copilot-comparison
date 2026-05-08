# Agent Instructions

You are building the ADLS Gen2 Lite Emulator described in DESIGN.md.

Work autonomously, but preserve the acceptance contract.

## Rules

- Do not use real Azure resources.
- Do not require Azure login.
- Do not remove or weaken acceptance tests.
- Do not skip the SDK smoke test.
- Prefer a small, working subset over a broad incomplete emulator.
- Keep Docker startup simple.
- Treat DESIGN.md as the product contract.
- Update README when behavior changes.

## Validation

Before declaring completion, run:

```bash
python -m compileall .
pytest -q
./scripts/evaluate.sh