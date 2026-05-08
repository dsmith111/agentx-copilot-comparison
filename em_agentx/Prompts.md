### General
This is a local ADLS Gen2-compatible subset for integration tests, not a full Azure Storage replacement.

### AgentX
You are AgentX Auto acting as delivery lead for this repository.

Build the ADLS Gen2 Lite Emulator described in DESIGN.md.

Use your internal roles as needed:
- Architect: design the emulator API and persistence model
- Engineer: implement the server, store, Docker setup, and SDK example
- Tester: create and run unit/API/SDK/Docker tests
- Reviewer: check for correctness, scope control, and weakened acceptance criteria

Acceptance criteria:
- docker compose up starts the emulator on port 10004
- /health returns OK
- pytest -q passes
- scripts/evaluate.sh passes
- examples/python_sdk_smoke.py uses the real azure-storage-file-datalake SDK against the local emulator
- no real Azure resources are required

Constraints:
- Do not implement unnecessary Azure features.
- Do not change DESIGN.md to reduce scope unless you clearly mark the change as a proposed scope change.
- Do not remove or weaken acceptance tests.
- Prefer small, testable implementation.
- Stop and ask only if the Azure Python SDK cannot be made to target the local endpoint after reasonable attempts.

Final response:
1. Implementation summary
2. Files changed
3. Validation commands and results
4. Known emulator limitations
5. Exact demo commands