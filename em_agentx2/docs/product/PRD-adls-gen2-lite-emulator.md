# PRD: ADLS Gen2 Lite Emulator

- Status: Draft
- Owner: Product Manager (AgentX)
- Sources of truth: [DESIGN.md](../../Design.md), [AGENTS.md](../../Agents.md), [WORK-ORDER](../agentx/WORK-ORDER-adls-gen2-lite-emulator.md)

## 1. Problem Statement

Developers building data pipelines against Azure Data Lake Storage Gen2 (ADLS Gen2) cannot run fast, deterministic, offline integration tests today. Azurite emulates Blob/Queue/Table but does not implement the ADLS Gen2 hierarchical namespace (HNS) `dfs` endpoint shape that the `azure-storage-file-datalake` SDK calls. The alternatives - hitting a real storage account or mocking the SDK - are slow, costly, network-dependent, or low-fidelity.

We need a local, Dockerized emulator that speaks enough of the ADLS Gen2 REST surface for the real Azure Python SDK to drive a complete file lifecycle, with no Azure account and no network egress.

## 2. Target Users

- **Primary**: Backend / data engineers writing Python code against `azure-storage-file-datalake` who need offline integration tests.
- **Secondary**: CI pipelines that must run ADLS-touching tests without Azure credentials.
- **Tertiary**: Workshop / training environments and demos that must work on a laptop with no Azure tenant.

Out of audience: production workloads, multi-tenant SaaS, anyone needing Azure feature parity (ACLs, leases, OAuth, billing).

## 3. Research Summary

- **Prior art reviewed**:
  - **Azurite** (Microsoft official Blob/Queue/Table emulator): no `dfs` endpoint, no HNS path semantics. Confirms the gap this product fills.
  - **LocalStack** (AWS): proves the "local emulator for cloud SDK" pattern is viable and widely adopted in CI.
  - **Real Azure Storage account with HNS enabled**: the parity baseline; we deliberately implement only the subset listed in DESIGN.md.
  - **Mock-based testing of the SDK**: low fidelity, drifts from SDK upgrades, does not catch wire-level regressions.
- **Key finding**: The Azure Python SDK exercises a small, stable subset of `dfs` verbs (PUT filesystem, PUT path resource=directory|file, PATCH action=append|flush, GET, HEAD, LIST, PUT mode=rename, DELETE). Implementing exactly that subset is sufficient and bounded.
- **Compliance / standards**: No regulated data is handled; emulator is dev-only and MUST NOT be marketed as production-safe.
- **User-need evidence**: Documented in DESIGN.md and the work order; team has explicit need driving this build. Broader user validation is an explicit assumption (see Section 11).
- **Model Council**: Skipped. Scope, success metric, and priority are already fixed by the DESIGN.md product contract and the work order acceptance criteria; council deliberation would not change the requirement set.

## 4. Goals & Success Metrics

| Goal | Metric | Target |
|------|--------|--------|
| SDK fidelity for the documented lifecycle | `examples/python_sdk_smoke.py` against running container | 100% pass |
| Offline / deterministic | External network calls during test run | 0 (only package install) |
| Fast local feedback | `pytest -q` wall time on dev laptop | < 30 s |
| One-command bring-up | Steps from clean clone to green smoke test | `docker compose up` + 1 script |
| End-to-end gate | `scripts/evaluate.sh` exit code | 0 |

## 5. MVP Scope

### 5.1 Filesystem operations
- Create filesystem
- List filesystems (account-scope GET)
- Delete filesystem

### 5.2 Path operations
- Create directory
- Create file (with `If-None-Match: *` honored)
- Append bytes (`PATCH action=append&position=N`)
- Flush bytes (`PATCH action=flush&position=N`)
- Read file (`GET`, including range reads sufficient for SDK download)
- Get properties (`HEAD`)
- List paths (recursive and non-recursive, with optional `directory=` filter)
- Rename file or directory (`PUT mode=rename&renameSource=...`)
- Delete file or directory (with `recursive` semantics)

### 5.3 Runtime
- Dockerfile + `docker-compose.yml`
- Listens on `127.0.0.1:10004`
- `/health` returns `OK`
- Persistent volume for filesystem + path data
- In-memory mode toggle for unit/API tests
- Permissive auth: any `Authorization` header is accepted; SharedKey signatures are not validated
- Account name fixed to `devstoreaccount1` for SDK compatibility

## 6. Non-Goals

The following are explicitly out of scope and MUST NOT be implemented in MVP:

- Azure Blob, Queue, Table, or Files APIs
- OAuth / Microsoft Entra ID / SharedKey signature verification
- POSIX ACL enforcement and RBAC
- Leases, soft delete, versioning, snapshots, change feed
- Encryption scopes, customer-managed keys
- Billing, quota, account management endpoints
- Byte-for-byte Azure error XML parity
- Delta Lake transaction protocol
- Multi-account, multi-region, geo-replication
- Any feature requiring a live Azure resource

## 7. Functional Requirements

| ID | Priority | Requirement |
|----|----------|-------------|
| FR-1 | P0 | `PUT /{filesystem}?resource=filesystem` creates a filesystem; repeat returns 409. |
| FR-2 | P0 | `DELETE /{filesystem}` removes filesystem and all paths. |
| FR-3 | P0 | `GET /?resource=account` lists filesystems. |
| FR-4 | P0 | `PUT /{filesystem}/{path}?resource=directory` creates a directory node. |
| FR-5 | P0 | `PUT /{filesystem}/{path}?resource=file` creates a zero-byte file; `If-None-Match: *` MUST 409 if the file already exists. |
| FR-6 | P0 | `PATCH ...?action=append&position=N` appends bytes at offset N; mismatched position returns 400. |
| FR-7 | P0 | `PATCH ...?action=flush&position=N` finalizes content length to N; subsequent reads return exactly N bytes. |
| FR-8 | P0 | Repeated append + flush cycles on the same file are supported and produce a consistent final byte stream. |
| FR-9 | P0 | `GET /{filesystem}/{path}` returns file bytes; supports range reads sufficient for SDK `download_file`. |
| FR-10 | P0 | `HEAD /{filesystem}/{path}` returns properties (size, type, etag-ish) for files and directories. |
| FR-11 | P0 | `GET /{filesystem}?resource=filesystem&recursive=...&directory=...` lists paths; honors `recursive` and `directory` filters. |
| FR-12 | P0 | `PUT /{filesystem}/{path}?mode=rename&renameSource=...` atomically renames a file or directory; old path MUST NOT be reachable after rename. |
| FR-13 | P0 | `DELETE /{filesystem}/{path}` deletes a path; non-empty directory without `recursive=true` returns 409. |
| FR-14 | P0 | Creating a child path under an existing file MUST fail (file is a leaf, not a container). |
| FR-15 | P0 | Operations against a deleted file MUST surface as `ResourceNotFoundError` through the SDK (HTTP 404 with body the SDK can parse). |
| FR-16 | P0 | `/health` returns HTTP 200 with body `OK`. |
| FR-17 | P1 | Error responses include a useful text/JSON body even if not byte-for-byte Azure XML. |
| FR-18 | P1 | All requests are tolerant of either `/{account}/{filesystem}/{path}` or `/{filesystem}/{path}` URL shapes. |

## 8. SDK Compatibility Requirements

| ID | Priority | Requirement |
|----|----------|-------------|
| SDK-1 | P0 | The unmodified `azure-storage-file-datalake` Python SDK MUST drive the full lifecycle in `examples/python_sdk_smoke.py`: create filesystem -> create directory -> create file -> append -> flush -> download -> list -> rename -> delete file -> delete filesystem. |
| SDK-2 | P0 | SDK clients constructed with `account_url="http://127.0.0.1:10004/devstoreaccount1"` and any non-empty credential MUST succeed. |
| SDK-3 | P0 | SDK `ResourceExistsError` MUST be raised when re-creating a file with `If-None-Match: *`. |
| SDK-4 | P0 | SDK `ResourceNotFoundError` MUST be raised when reading a deleted file. |
| SDK-5 | P0 | No code change or monkey-patch to the SDK is permitted to make the smoke test pass. |
| SDK-6 | P1 | The smoke test MUST be runnable both inside CI and on a developer laptop with only `pip install` and a running container. |

## 9. Docker / Runtime Requirements

| ID | Priority | Requirement |
|----|----------|-------------|
| RT-1 | P0 | `docker compose up` starts the emulator with no extra flags or env required. |
| RT-2 | P0 | Server listens on TCP `10004` on the host. |
| RT-3 | P0 | Account name is `devstoreaccount1` and is stable across restarts. |
| RT-4 | P0 | A named Docker volume persists path data; data MUST survive `docker compose restart`. |
| RT-5 | P0 | An in-memory mode (env flag) is available for unit/API tests and avoids touching disk. |
| RT-6 | P0 | No outbound network calls at runtime; only image build / `pip install` may reach the network. |
| RT-7 | P1 | Container image build is reproducible from the committed Dockerfile with no host-specific state. |
| RT-8 | P1 | Startup to healthy `/health` MUST complete within 60 s on a typical dev laptop (matches `evaluate.sh` poll budget). |

## 10. Acceptance Criteria

The product is accepted when **all** of the following hold on a clean clone:

1. `docker compose up` starts the emulator and `curl http://127.0.0.1:10004/health` returns `OK`.
2. `pytest -q` passes (unit tests for the store + HTTP API tests).
3. `python examples/python_sdk_smoke.py` passes against the running container using the real Azure SDK.
4. `scripts/evaluate.sh` exits 0 end-to-end.
5. README documents SDK configuration (account URL, account name, sample snippet).
6. No Azure credentials, no Azure resources, and no network egress beyond package installation are required.
7. Hidden edge cases from the work order are covered by tests:
   - repeated append/flush
   - child path under a file parent fails
   - `If-None-Match: *` on existing file fails
   - deleted file surfaces as `ResourceNotFoundError` via SDK
   - persistence survives container restart
   - old path missing after rename

## 11. Risks & Assumptions

### Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| R-1 | SDK version drift changes wire format and breaks emulator | Medium | High | Pin `azure-storage-file-datalake` version in `examples/` and tests; add a CI job that runs the smoke test on the pinned version. |
| R-2 | Subtle Azure semantics (etag, content-md5, lease headers) cause SDK to throw despite "happy path" working | Medium | Medium | Implement minimum etag/properties needed by SDK paths exercised in smoke test; out-of-scope features return ignorable defaults. |
| R-3 | Users mistake the emulator for production-grade Azure | Low | High | README + landing doc clearly mark "local development only, not for production"; non-goals are explicit. |
| R-4 | Persistence layout changes break dev volumes across versions | Medium | Low | Versioned data dir or migration note; in-memory mode is the source of truth for tests. |
| R-5 | Permissive auth could be exposed accidentally on a network interface | Low | Medium | Default bind to `127.0.0.1`; document that this MUST NOT be exposed publicly. |
| R-6 | Range-read semantics differ from Azure for large files | Medium | Low | MVP scopes range reads to what `download_file` actually issues; document deviations. |

### Assumptions

- The `azure-storage-file-datalake` SDK uses the documented `dfs` REST shape and does not require SharedKey signature validation when given a permissive endpoint.
- The HTTP verbs and query parameters listed in DESIGN.md are sufficient for the lifecycle operations enumerated in SDK-1.
- Developers running tests have Docker and Python available locally; no Windows-only or Azure-only tooling is required.
- The team accepts "useful error text" instead of byte-for-byte Azure error XML for MVP.
- A single account (`devstoreaccount1`) is sufficient; multi-account support is not needed.
- No regulated or production data will ever be stored in the emulator.

## 12. Out of Scope (Restated)

See Section 6. Anything not listed in Sections 5, 7, 8, or 9 is out of scope for MVP and MUST be deferred to a follow-up PRD.

## 13. Open Questions

- Q-1: Do we need to emit a minimal etag header for files so the SDK's optimistic concurrency code paths do not fail on edge operations not in the smoke test? (Default assumption: yes, generate an opaque incrementing etag.)
- Q-2: Should `recursive=true` deletes return per-path results, or is a single 200 sufficient for the SDK? (Default assumption: single 200 is sufficient based on SDK behavior; verify in implementation.)
- Q-3: Is a CLI for managing the emulator (e.g., `adls-lite reset`) desired in MVP? (Default assumption: no; `docker compose down -v` is sufficient.)

## 14. Appendix

- Endpoint surface, data model, and persistence requirements are mirrored from [DESIGN.md](../../Design.md) and are the authoritative wire-level contract.
- Required deliverable artifacts are listed in [WORK-ORDER-adls-gen2-lite-emulator.md](../agentx/WORK-ORDER-adls-gen2-lite-emulator.md).
- Agent execution constraints (no real Azure, do not weaken tests, keep Docker startup simple) are defined in [AGENTS.md](../../Agents.md).
