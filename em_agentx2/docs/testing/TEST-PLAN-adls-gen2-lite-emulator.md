# Test Plan: ADLS Gen2 Lite Emulator

- Status: Ready before implementation
- Date: 2026-05-06
- Audience: Engineer, Tester, Reviewer, DevOps
- Inputs: [DESIGN.md](/home/smithdavi/ai-learning/agentx/em_agentx2/Design.md), [WORK-ORDER-adls-gen2-lite-emulator.md](/home/smithdavi/ai-learning/agentx/em_agentx2/docs/agentx/WORK-ORDER-adls-gen2-lite-emulator.md), [PRD-adls-gen2-lite-emulator.md](/home/smithdavi/ai-learning/agentx/em_agentx2/docs/product/PRD-adls-gen2-lite-emulator.md), [ADLS-GEN2-SDK-COMPATIBILITY-NOTES.md](/home/smithdavi/ai-learning/agentx/em_agentx2/docs/research/ADLS-GEN2-SDK-COMPATIBILITY-NOTES.md), [ADR-adls-gen2-lite-emulator.md](/home/smithdavi/ai-learning/agentx/em_agentx2/docs/architecture/ADR-adls-gen2-lite-emulator.md), [SPEC-adls-gen2-lite-emulator.md](/home/smithdavi/ai-learning/agentx/em_agentx2/docs/architecture/SPEC-adls-gen2-lite-emulator.md)

## 1. Purpose

This plan defines the verification strategy and certification gates for the ADLS Gen2 Lite Emulator before any emulator implementation exists. It locks the required test layers, the minimum behaviors that must be covered, the acceptance-blocking edge cases, and the evidence needed for later certification.

The plan is intentionally stricter than a smoke checklist. The emulator is only acceptable when the real Azure Python SDK succeeds against the local endpoint and the hidden edge cases in the work order are proven by executable tests.

## 2. Test Objectives

- Verify the hierarchical store invariants defined in the spec before HTTP behavior is considered.
- Verify direct HTTP route behavior matches the documented REST subset for filesystem and path operations.
- Verify the unmodified `azure-storage-file-datalake` SDK drives the documented lifecycle end to end.
- Verify the Dockerized runtime starts cleanly, exposes `/health`, persists flushed data across restart, and supports the SDK smoke test.
- Verify all required negative cases produce the expected HTTP status and SDK exception class.

## 3. Scope

### 3.1 In scope

- Filesystem create, list, delete.
- Directory create.
- File create.
- Append and flush semantics.
- Read and HEAD semantics needed by SDK download.
- Path listing with recursive and non-recursive behavior.
- Rename behavior.
- Delete behavior.
- Persistence across Docker restart for flushed data.
- Error mapping sufficient for `ResourceExistsError` and `ResourceNotFoundError`.

### 3.2 Out of scope

- ACLs, leases, soft delete, snapshots, versioning.
- OAuth, SAS, SharedKey signature validation.
- Blob, Queue, Table, and Azure Files APIs.
- Performance, load, and security certification beyond the dev-tool scope documented in the PRD.

## 4. Test Pyramid

| Layer | Purpose | Execution mode | Primary artifacts |
|------|---------|----------------|-------------------|
| Store unit tests | Validate tree, file, rename, delete, append/flush invariants without HTTP | `pytest -q tests/test_store.py` | `tests/test_store.py` |
| Direct HTTP API tests | Validate route mapping, status codes, headers, and error envelopes | `pytest -q tests/test_api.py` | `tests/test_api.py` |
| Azure SDK smoke test | Validate the real Python SDK against the emulator contract | `pytest -q tests/test_sdk_smoke.py` and `python examples/python_sdk_smoke.py` | `tests/test_sdk_smoke.py`, `examples/python_sdk_smoke.py` |
| Docker smoke test | Validate container build, health endpoint, real HTTP wiring, and restart persistence | `scripts/evaluate.sh` plus restart check | `scripts/evaluate.sh`, future DevOps validation artifact |

## 5. Entry and Exit Criteria

### 5.1 Entry criteria

- PRD, research notes, ADR, and spec exist and are internally consistent.
- The chosen stack is fixed: Python 3.12, FastAPI, in-memory store for tests, snapshot persistence for Docker.
- The route table and data-model invariants are fixed in the spec.
- The SDK version to target is pinned in implementation work.

### 5.2 Exit criteria

- `python -m compileall src tests examples` passes.
- `pytest -q` passes with store, API, and SDK smoke tests enabled.
- `docker compose up -d` starts the emulator on port `10004`.
- `curl -fsS http://127.0.0.1:10004/health` returns `OK`.
- `python examples/python_sdk_smoke.py` passes against the running container.
- Persistence survives `docker compose restart` for flushed file content.
- `scripts/evaluate.sh` passes end to end.

## 6. Test Environment

| Environment | Purpose | Mode |
|------------|---------|------|
| Local Python process | Fast feedback for unit and API tests | In-memory store |
| ASGI in-process app | HTTP contract validation without Docker | In-memory store |
| Docker container | Runtime validation and persistence checks | Snapshot store + named volume |
| Loopback host endpoint | Real SDK smoke against `http://127.0.0.1:10004/devstoreaccount1` | Docker runtime |

Constraints:

- No live Azure resource may be used.
- Tests must not require Azure login or Azure credentials.
- Tests must clean up their filesystems and paths.
- Stable account name is `devstoreaccount1`.

## 7. Store Unit Test Plan

Store tests validate internal invariants before route wiring. They are the fastest way to catch data-model mistakes.

### 7.1 Coverage areas

| Area | Required checks |
|------|-----------------|
| Filesystem lifecycle | Create unique filesystem, reject duplicate, delete existing, reject missing delete |
| Path tree invariants | Directory parent/child relationships, file leaf behavior, path lookup correctness |
| File creation | Create zero-byte file, reject duplicate file under `If-None-Match: *`, reject file under file parent |
| Append/flush | Append at exact offset only, reject offset mismatch, flush only at current tail, repeated append/flush extends bytes |
| Read | Read committed bytes only, no unflushed bytes visible |
| Listing | Recursive and non-recursive traversal, prefix scoping by directory |
| Rename | File rename preserves bytes, directory rename moves subtree atomically, old path missing after rename |
| Delete | Delete file, delete empty dir, reject non-empty dir without recursive, recursive delete subtree |
| Persistence contract | Snapshot round-trip preserves committed bytes and tree metadata |

### 7.2 Required unit tests

- Create and delete filesystem successfully.
- Reject duplicate filesystem creation.
- Create directory tree and resolve nodes by path.
- Reject child path creation under file parent.
- Create file with create-only semantics.
- Reject create file when `If-None-Match: *` targets an existing file.
- Append once and flush once.
- Append twice, flush twice, and verify concatenated bytes.
- Reject append at wrong position.
- Reject flush at wrong position.
- Read file returns only committed bytes.
- Rename file preserves content and removes source path.
- Rename directory moves descendants.
- Delete file makes subsequent lookup fail.
- Reject non-recursive delete of non-empty directory.

## 8. Direct HTTP API Test Plan

API tests validate the wire contract independently from the SDK. They anchor status codes, response headers, and error envelopes so SDK failures are easier to diagnose.

### 8.1 Coverage areas

| Route family | Required checks |
|-------------|-----------------|
| `/health` | 200 and body `OK` |
| Filesystem routes | PUT create, DELETE delete, GET list |
| Directory routes | PUT with `resource=directory` |
| File routes | PUT with `resource=file`, GET, HEAD, DELETE |
| Append/flush routes | PATCH append and flush with position checks |
| Listing route | GET filesystem listing with `recursive` and `directory` |
| Rename route | PUT `mode=rename` with header and legacy query support |
| Error envelope | Canonical JSON shape with `error.code` and `error.message` |
| Response headers | `x-ms-request-id`, `x-ms-version`, `ETag`, `Last-Modified`, resource type, content length |

### 8.2 Required API tests

- `GET /health` returns `OK`.
- `PUT /{filesystem}?resource=filesystem` returns created status.
- Duplicate filesystem create returns 409 with `FilesystemAlreadyExists`.
- `PUT /{filesystem}/{path}?resource=directory` creates directory.
- `PUT /{filesystem}/{path}?resource=file` creates file.
- Recreate same file with `If-None-Match: *` returns 409 with `PathAlreadyExists`.
- Creating a child path under a file parent returns 409 with `PathConflict`.
- Append request with correct position succeeds.
- Append request with wrong position returns 400.
- Flush request at current tail succeeds.
- Flush request past current tail returns 400.
- GET file returns committed bytes.
- HEAD file returns length, etag, last-modified, and `x-ms-resource-type=file`.
- Recursive list returns full subtree.
- Non-recursive list returns only immediate children.
- Rename using `x-ms-rename-source` succeeds.
- Old path returns 404 after rename.
- Delete missing path returns 404 with `PathNotFound`.
- Delete non-empty directory without `recursive=true` returns 409.

## 9. Azure SDK Smoke Test Plan

The SDK smoke test is the contract test. It proves that the emulator is useful in the exact integration scenario it is intended to support.

### 9.1 Required lifecycle

The smoke test must use the unmodified `azure-storage-file-datalake` Python SDK and execute:

1. Create filesystem.
2. Create directory.
3. Create file.
4. Append bytes in at least two chunks.
5. Flush bytes.
6. Download and verify content.
7. List paths and verify created entries.
8. Rename file.
9. Verify old path is missing.
10. Delete file.
11. Verify deleted file raises `ResourceNotFoundError`.
12. Delete filesystem.

### 9.2 Required SDK-focused assertions

- `ResourceExistsError` is raised when create-only file creation targets an existing file.
- `ResourceNotFoundError` is raised after deleting a file and attempting to read it.
- SDK client works with account URL `http://127.0.0.1:10004/devstoreaccount1` and any non-empty credential.
- No monkey-patching, transport substitution, or SDK code edits are used to make the test pass.

## 10. Docker Smoke and Persistence Plan

Docker validation proves the runtime packaging, port mapping, health endpoint, and persistence contract.

### 10.1 Required Docker checks

| Check | Expected result |
|------|-----------------|
| `docker compose build` | Image builds reproducibly |
| `docker compose up -d` | Emulator starts and binds host port `10004` |
| `/health` probe | Returns `OK` within 60 seconds |
| SDK smoke against container | Passes with real SDK |
| Restart persistence | Flushed file remains readable after `docker compose restart` |
| Teardown | `docker compose down -v` cleans the environment |

### 10.2 Restart persistence scenario

1. Start container.
2. Create filesystem, directory, and file.
3. Append and flush known bytes.
4. Restart container without removing volume.
5. Re-read the file via HTTP or SDK.
6. Assert exact byte preservation.

Uncommitted append buffers are explicitly out of scope for restart guarantees. Only flushed data is certification-blocking.

## 11. Acceptance-Blocking Edge Cases

Each work-order edge case must map to at least one executable test.

| Edge case | Layer | Expected outcome |
|----------|-------|------------------|
| Repeated append/flush | Store + API + SDK | Final bytes equal concatenation across multiple cycles |
| Child path under file parent must fail | Store + API | Conflict status / path conflict error |
| `If-None-Match: *` on existing file must fail | Store + API + SDK | Conflict status and `ResourceExistsError` through SDK |
| Deleted file should raise `ResourceNotFoundError` through SDK | SDK | Exact SDK exception class |
| Persistence survives Docker restart | Docker smoke | Flushed bytes survive restart |
| Old path missing after rename | Store + API + SDK | Source path 404 / `ResourceNotFoundError` |

## 12. Traceability Matrix

| Requirement / contract | Planned test layer |
|------------------------|--------------------|
| FR-1, FR-2, FR-3 filesystem lifecycle | Store, API |
| FR-4, FR-5 create directory/file | Store, API, SDK |
| FR-6, FR-7 append/flush semantics | Store, API, SDK |
| FR-8 repeated append/flush | Store, API, SDK |
| FR-9, FR-10 read and head | API, SDK |
| FR-11 listing | Store, API, SDK |
| FR-12 rename | Store, API, SDK |
| FR-13 delete semantics | Store, API, SDK |
| FR-14 child under file parent fails | Store, API |
| FR-15 deleted file -> `ResourceNotFoundError` | SDK |
| FR-16 `/health` | API, Docker |
| RT-4 persistence survives restart | Docker |
| SDK-1 through SDK-5 real SDK compatibility | SDK, Docker |

## 13. Certification Strategy

Certification is deferred until implementation exists, but the gates are fixed now so the team cannot lower them later.

### 13.1 Certification gates

| Gate | Threshold | Blocks release |
|------|-----------|----------------|
| Unit/store tests | 100% pass | Yes |
| Direct API tests | 100% pass | Yes |
| SDK smoke tests | 100% pass | Yes |
| Docker smoke and restart persistence | 100% pass | Yes |
| `pytest -q` | Exit code 0 | Yes |
| `scripts/evaluate.sh` | Exit code 0 | Yes |

### 13.2 Required certification evidence

- Test run output for `pytest -q`.
- Test run output for `python examples/python_sdk_smoke.py`.
- Docker health probe result.
- Evidence of restart persistence run.
- Final certification report to be written later at [docs/testing/CERTIFICATION-adls-gen2-lite-emulator.md](/home/smithdavi/ai-learning/agentx/em_agentx2/docs/testing/CERTIFICATION-adls-gen2-lite-emulator.md).

## 14. Test Data Strategy

- Use deterministic filesystem, directory, and file names scoped per test.
- Use ASCII-only payloads and known byte sequences.
- Prefer short byte strings for unit and API tests.
- Use at least one multi-chunk payload in SDK smoke tests to force append/flush behavior.
- Tests must clean up created filesystems unless a specific failure scenario requires post-failure inspection.

## 15. Tooling and Commands

Planned validation commands after implementation:

```bash
python -m compileall src tests examples
pytest -q
docker compose build
docker compose up -d
curl -fsS http://127.0.0.1:10004/health
python examples/python_sdk_smoke.py
scripts/evaluate.sh
```

## 16. Initial Test Skeleton Policy

Before emulator implementation exists, the test modules may be present as scaffold files that:

- define the intended test groups,
- avoid importing missing implementation modules at import time, and
- skip cleanly with a clear reason until the emulator modules exist.

Once implementation begins, these scaffolds must be converted into real executable tests. Placeholder skips are acceptable only before the emulator code is added.

## 17. Risks and Watchpoints

| Risk | Test response |
|------|---------------|
| SDK drift changes request shape | Keep a pinned SDK in smoke tests and assert both rename-source forms in API tests |
| Response headers missing cause SDK failure | Add header assertions in API tests before SDK debugging |
| Persistence behavior flakes | Keep Docker restart check isolated and deterministic |
| Over-reliance on SDK smoke hides local store bugs | Maintain separate store and API layers with direct assertions |

## 18. Deliverables from This Phase

- This test plan.
- Initial scaffold files at [tests/test_store.py](/home/smithdavi/ai-learning/agentx/em_agentx2/tests/test_store.py), [tests/test_api.py](/home/smithdavi/ai-learning/agentx/em_agentx2/tests/test_api.py), and [tests/test_sdk_smoke.py](/home/smithdavi/ai-learning/agentx/em_agentx2/tests/test_sdk_smoke.py).

No emulator implementation is included in this phase.