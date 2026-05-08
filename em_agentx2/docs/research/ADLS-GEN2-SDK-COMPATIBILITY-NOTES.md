# Research: Azure SDK and ADLS Gen2 Protocol Compatibility Notes

- Status: Research / Spike output (input to ADR + Tech Spec)
- Audience: Architect, Engineer, Tester
- Sources: [DESIGN.md](../../Design.md), [WORK-ORDER](../agentx/WORK-ORDER-adls-gen2-lite-emulator.md), [PRD](../product/PRD-adls-gen2-lite-emulator.md), Microsoft REST docs for "Azure Data Lake Storage Gen2 - Path / Filesystem", `azure-sdk-for-python` repository (`azure-storage-file-datalake`).
- Verification date: 2026-05-06.
- Confidence tags appear inline per finding.

---

## 1. Scope of Research

What the emulator must satisfy:

- Drive the unmodified `azure-storage-file-datalake` Python SDK through: create filesystem -> create dir -> create file -> append -> flush -> read -> list -> rename -> delete file -> delete filesystem.
- Honor hidden edge cases: repeated append/flush, child-under-file rejection, `If-None-Match: *` conflict, `ResourceNotFoundError` on deleted file, persistence across container restart, source path absent after rename.
- No live Azure resource. No SDK monkey-patch.

What this document does NOT decide:

- Storage backend choice, framework choice, persistence layout. Those are ADR/Spec concerns.
- Test plan. That belongs in the Test Plan artifact.

---

## 2. Two URL Surfaces the SDK May Use

Confidence: HIGH

The Python `DataLakeServiceClient` is constructed against a `dfs` style account URL, but internally the SDK is layered on top of the Blob client and may call BOTH endpoints depending on the operation.

| Surface | Host pattern (real Azure) | Used for |
|---------|---------------------------|----------|
| `dfs` (Data Lake) | `https://<account>.dfs.core.windows.net` | Filesystem CRUD, path CRUD, append, flush, rename, list paths, get/set ACLs (out of scope). |
| `blob` (Blob) | `https://<account>.blob.core.windows.net` | Some property reads, range downloads on file content, container-level fallbacks in older SDK paths. |

**Implication for the emulator**:

- The emulator MUST accept requests on a single host:port (`127.0.0.1:10004`) and route by **path + query**, not by host.
- The emulator MUST tolerate either of:
  - `/{account}/{filesystem}/{path}` (when the SDK includes the account in the path, typical for emulator-style URLs)
  - `/{filesystem}/{path}` (when the SDK strips it)
- The emulator SHOULD also tolerate the SDK calling routes that look "blob-like" for download/read of file content. In practice for the documented lifecycle, `download_file` issues `GET /{filesystem}/{path}` against the dfs host, but range-read fallbacks observed in SDK source can target the blob endpoint shape `/{filesystem}/{path}` with no `?resource=` query. Treating these as equivalent is the safest design.

Account-style URL in tests:

```
http://127.0.0.1:10004/devstoreaccount1
```

The fixed `devstoreaccount1` matches the Azurite convention and is what most SDK examples use for emulator endpoints. (Confidence: HIGH)

---

## 3. Expected SDK Request Patterns by Operation

Below is the expected wire behavior the emulator must satisfy. "Method" is HTTP method, "Path" is relative to the account base URL, "Query" lists the discriminating parameters.

### 3.1 Filesystem lifecycle

| SDK call | Method | Path | Key query | Notes |
|----------|--------|------|-----------|-------|
| `service.create_file_system(name)` | PUT | `/{filesystem}` | `?resource=filesystem` | 201 on create, 409 if already exists. |
| `service.list_file_systems()` | GET | `/` | `?resource=account&comp=list` | Returns filesystem listing. SDK is tolerant of either XML or JSON-ish bodies as long as parser sees container entries. |
| `service.delete_file_system(name)` | DELETE | `/{filesystem}` | (none required) | 202/200 on success, 404 if missing. |

Confidence: HIGH for PUT/DELETE shape. MEDIUM for the exact list response body required - emulator MAY return a minimal Azure-style XML envelope; the smoke test does not strictly require listing filesystems, only paths.

### 3.2 Directory and file creation

| SDK call | Method | Path | Key query | Notes |
|----------|--------|------|-----------|-------|
| `fs.create_directory(path)` | PUT | `/{filesystem}/{path}` | `?resource=directory` | Creates a directory node. |
| `dir.create_file(name)` / `fs.get_file_client(p).create_file()` | PUT | `/{filesystem}/{path}` | `?resource=file` | Creates a zero-length file. SDK MAY send `If-None-Match: *` to enforce "create only if not exists". |

**Edge case**: If an existing file is recreated with `If-None-Match: *`, server MUST return 409. The SDK maps that to `ResourceExistsError`. (Confidence: HIGH)

**Edge case**: Creating any path whose parent resolves to a file MUST return 409 (or 404 with a clear "parent is a file" message). Files are leaves. (Confidence: HIGH)

### 3.3 Append + Flush (the two-phase write)

Confidence: HIGH. This is the most distinctive ADLS Gen2 contract.

`upload_data(...)` and the lower-level `append_data` + `flush_data` calls produce this sequence:

1. `PUT /{filesystem}/{path}?resource=file` -> create empty file (status 201).
2. One or more `PATCH /{filesystem}/{path}?action=append&position=N` requests:
   - Body is the raw bytes for that chunk.
   - `position` is the byte offset where this chunk begins (NOT the new end-of-stream).
   - `Content-Length` MUST equal the chunk size.
3. One `PATCH /{filesystem}/{path}?action=flush&position=N` to commit:
   - `position` is the **total** length of the file after appending all uncommitted chunks.
   - Body is empty (or zero-length).
   - Response 200/202 with updated properties.

**Server invariants the emulator MUST hold**:

- Bytes written by `append` are uncommitted until a `flush` covers their range.
- A `flush` with `position=N` finalizes file length to exactly `N`. Subsequent reads MUST return exactly `N` bytes.
- A second append/flush cycle on the same file extends the file (it is NOT a truncate-and-rewrite). Repeated cycles MUST work correctly (covers the "repeated append/flush" hidden edge case).
- An append whose `position` does not equal the current uncommitted-tail offset MUST return 400. The SDK relies on this to surface client bugs.
- A flush whose `position` exceeds the bytes appended so far MUST return 400.
- A flush whose `position` is less than appended bytes MAY truncate uncommitted tail bytes (Azure behavior); for MVP the emulator MAY simplify by also returning 400, since the SDK never issues that pattern in normal use.

Implementation implication: the store needs a per-file (committed_bytes, uncommitted_buffer) pair. Flush atomically promotes the buffer and updates length.

### 3.4 Read / download

| SDK call | Method | Path | Key headers | Notes |
|----------|--------|------|-------------|-------|
| `file.download_file().readall()` | GET | `/{filesystem}/{path}` | Optional `Range: bytes=START-END` | Returns file bytes. May issue multiple range reads in chunks. |
| `file.get_file_properties()` | HEAD | `/{filesystem}/{path}` | - | Returns `Content-Length`, `Last-Modified`, `ETag`, `x-ms-resource-type: file`. |

**Range semantics** (Confidence: MEDIUM):

- `Range: bytes=0-1023` MUST return bytes 0..1023 inclusive (1024 bytes), with status `206 Partial Content` and `Content-Range: bytes 0-1023/<total>`.
- A request with no `Range` header MUST return the entire content with 200.
- The SDK's `download_file` defaults to a single-shot read for small files but may chunk large files. For the smoke test the file is small; partial-content support is recommended but not strictly required for MVP.

### 3.5 List paths

Confidence: HIGH on shape, MEDIUM on response body fidelity.

`fs.get_paths(path=None, recursive=True)` issues:

```
GET /{filesystem}?resource=filesystem&recursive=true|false[&directory={prefix}][&continuation=...][&maxResults=...]
```

Response body is JSON of the form:

```
{ "paths": [ { "name": "...", "isDirectory": "true"|absent, "contentLength": "N", "lastModified": "...", "etag": "..." }, ... ]
```

**Server invariants**:

- `recursive=true` MUST return the entire subtree; `recursive=false` MUST return only direct children.
- `directory=foo/bar` MUST scope listing to that subtree.
- Continuation tokens are NOT required for MVP (smoke test list is small). If omitted, the server SHOULD NOT return `x-ms-continuation`.

### 3.6 Rename

Confidence: HIGH.

```
PUT /{filesystem}/{newPath}?mode=rename
x-ms-rename-source: /{filesystem}/{oldPath}
```

Notes:

- The SDK sends the source as a header (`x-ms-rename-source`), not always as a query param. Some older code paths use `?renameSource=`. The emulator SHOULD accept BOTH and prefer the header when both are present.
- For directories, rename MUST be atomic at the emulator level (rewrite a single parent pointer or move a subtree under a lock).
- After rename, the old path MUST 404 on subsequent ops (covers the "old path missing after rename" hidden edge case).
- Rename MAY require `If-None-Match: *` semantics if the destination exists; for MVP, fail with 409 if destination exists and no overwrite indicated.

### 3.7 Delete

Confidence: HIGH.

```
DELETE /{filesystem}/{path}?recursive=true|false
```

- Deleting a file: `recursive` is ignored.
- Deleting a non-empty directory without `recursive=true` MUST return 409.
- Deleting a missing path MUST return 404; the SDK maps that to `ResourceNotFoundError` (covers the hidden edge case).

---

## 4. Authentication Behavior

Confidence: HIGH.

- Production SDK supports SharedKey, SAS, and Entra ID tokens.
- Against a local dev endpoint, the SDK still attaches an `Authorization` header (typically a SharedKey signature computed from a fake key) plus `x-ms-date` and `x-ms-version`.
- The emulator MUST accept any non-empty `Authorization` header without validating the signature.
- The emulator MAY ignore `x-ms-version`, but SHOULD echo a recent supported version in the response (e.g., `x-ms-version: 2023-11-03`) to satisfy SDK header checks.

Credentials in tests are typically constructed with the well-known Azurite key:

```
AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==
```

Any string of the right shape suffices because the emulator does not validate.

---

## 5. Required Response Headers

Confidence: MEDIUM (the SDK is forgiving but watches for these).

For PUT/PATCH/HEAD on paths and files, the emulator SHOULD return:

| Header | When | Value strategy |
|--------|------|----------------|
| `ETag` | All path responses | Opaque incrementing token, e.g. `"0x8DA<counter>"`. Stable per-version of the resource. |
| `Last-Modified` | All path responses | RFC 1123 GMT timestamp. |
| `x-ms-request-id` | All responses | A new UUID per request; helps SDK logging. |
| `x-ms-version` | All responses | Echo a recent stable version (e.g., `2023-11-03`). |
| `x-ms-resource-type` | HEAD on paths | `file` or `directory`. |
| `Content-Length` | GET on file, HEAD on file | Exact committed length. |
| `Content-Range` | GET with `Range` header | `bytes START-END/TOTAL` with status 206. |

`Content-MD5` is NOT required; the SDK does not enforce it for the lifecycle in scope.

---

## 6. Error Behavior

Confidence: HIGH on status codes, MEDIUM on body format.

### 6.1 Status code map

| Condition | HTTP | SDK exception |
|-----------|------|---------------|
| Filesystem already exists | 409 | `ResourceExistsError` |
| Filesystem missing | 404 | `ResourceNotFoundError` |
| File created with `If-None-Match: *` and exists | 409 | `ResourceExistsError` |
| Path missing on read/HEAD/delete | 404 | `ResourceNotFoundError` |
| Append at wrong `position` | 400 | `HttpResponseError` |
| Flush past appended bytes | 400 | `HttpResponseError` |
| Delete non-empty dir without `recursive=true` | 409 | `HttpResponseError` |
| Create child path under a file parent | 409 (or 404) | `HttpResponseError` / `ResourceNotFoundError` |
| Bad request body / malformed query | 400 | `HttpResponseError` |
| Auth header missing entirely | 403 | `ClientAuthenticationError` (optional in MVP; permissive auth = always pass) |

### 6.2 Body format

The Python SDK parses error bodies leniently: it primarily branches on HTTP status, then attempts to read an Azure-style error body of either:

- XML: `<?xml ...?><Error><Code>...</Code><Message>...</Message></Error>`
- JSON: `{ "error": { "code": "...", "message": "..." } }`

For MVP the emulator MAY return the JSON shape uniformly; the SDK still raises the correct exception class because the class is chosen by status code. The body is used to populate the exception's `error_code` and `message` fields. (Confidence: HIGH)

The error `Code` value SHOULD match well-known Azure codes where the SDK or user code may inspect them:

| Condition | Recommended code string |
|-----------|-------------------------|
| Filesystem exists | `FilesystemAlreadyExists` |
| Filesystem missing | `FilesystemNotFound` |
| Path exists (`If-None-Match: *`) | `PathAlreadyExists` |
| Path missing | `PathNotFound` |
| Bad append/flush position | `InvalidFlushPosition` / `InvalidRange` |
| Non-empty dir delete | `DirectoryNotEmpty` |
| Parent is a file | `InvalidResourceName` or `PathConflict` |

---

## 7. Behaviors that DON'T need parity (can be ignored or stubbed)

Confidence: HIGH. Each of these is exercised by some Azure SDK code path but NOT by the documented MVP lifecycle, and supporting them is explicitly a non-goal per PRD Section 6.

- POSIX ACL get/set (`?action=getAccessControl|setAccessControl`)
- Lease operations (`x-ms-lease-*` headers and `?comp=lease`)
- Soft delete / undelete / versioning / snapshots
- Encryption scope headers (`x-ms-encryption-*`)
- Customer-provided keys (`x-ms-encryption-key*`)
- Static website / CORS preflight
- User delegation key endpoints
- Account properties (`?restype=service&comp=properties`)
- Change feed

The emulator MAY return 501 Not Implemented or a minimal 200 with empty body for these if any SDK path probes them; for MVP the cleanest default is 501 with a JSON error body.

---

## 8. Implementation Implications

These are findings to feed into the ADR and Tech Spec; they are NOT design decisions.

### 8.1 Routing

- Single host:port serves all surfaces. Discriminate handlers on `(method, path-shape, query-keys)`.
- Account prefix is optional in the path; strip it if present before dispatch.
- A small router table keyed on `(method, has-resource-query, action-query)` is sufficient. No HTTP host-based routing needed.

### 8.2 State model

- Per-filesystem tree of nodes. Node type in {directory, file}.
- Per-file state: `committed_bytes: bytes`, `uncommitted_buffer: bytes`, `etag`, `last_modified`.
- Rename = relink under a write lock; for directories, relink the subtree root.
- Delete with `recursive=true` walks subtree; otherwise reject if children exist.

### 8.3 Persistence

- Two modes: in-memory (tests) and on-disk (Docker volume).
- On-disk layout is implementation-defined; a snapshot-on-write or per-file file-on-disk strategy both satisfy "survives restart". This is an ADR decision.
- Persistence MUST capture committed file content + path tree metadata. Uncommitted append buffers MAY be lost on restart (Azure also does not guarantee uncommitted data across server restarts).

### 8.4 Concurrency

- For MVP, a single global write lock per filesystem is sufficient. Smoke test is single-client. ADR may revisit if concurrent client tests are added.
- Atomic rename and atomic flush MUST be guaranteed under that lock.

### 8.5 Headers and IDs

- ETag and request-id generation belong in a small middleware so all handlers emit consistent metadata.
- A single `x-ms-version` constant is fine; do not implement per-version branching.

### 8.6 Error envelope

- One helper that converts an internal error enum to (status, code-string, message) and writes a JSON `{ "error": { "code": ..., "message": ... } }` body. Drives FR-15 and several SDK exception mappings.

### 8.7 SDK pinning

- Pin the `azure-storage-file-datalake` version used by `examples/python_sdk_smoke.py` and any SDK-touching tests. SDK upgrades have historically shifted between query-arg vs header for `renameSource` and similar; pinning insulates the emulator from drift (Risk R-1 in PRD).

### 8.8 Bind address

- Default bind to `127.0.0.1` inside the container's published port mapping; document that exposing on `0.0.0.0` to a public network is unsafe because auth is permissive (PRD Risk R-5).

---

## 9. Open Questions to Resolve in ADR / Spec

- OQ-1: Final response body format for `list filesystems` - minimal XML, JSON, or both? (PRD Q-2 related.)
- OQ-2: Storage backend - per-file files-on-disk vs single SQLite vs JSON snapshot. Trade-off: simplicity vs atomicity vs inspectability.
- OQ-3: Whether to implement HTTP `Range` partial content in MVP or defer (smoke test does not require it; some SDK paths use it).
- OQ-4: Whether to emit a 501 or a 200-empty for out-of-scope endpoints if the SDK probes them defensively.
- OQ-5: Whether account name should be configurable via env var, or hard-coded to `devstoreaccount1`.

---

## 10. Confidence Summary

| Area | Confidence |
|------|-----------|
| Required HTTP verbs and query shape | HIGH |
| Append/flush two-phase semantics | HIGH |
| Rename via header `x-ms-rename-source` | HIGH |
| Status code -> SDK exception mapping | HIGH |
| Permissive auth is sufficient | HIGH |
| Exact list-paths JSON shape | MEDIUM |
| Need for `Range` partial content | MEDIUM |
| Need for byte-perfect Azure error XML | LOW (PRD explicitly waives) |
| Need for ACL/lease/encryption headers | LOW (out of scope) |

---

## 11. Inputs Reviewed

- [DESIGN.md](../../Design.md) - product contract and endpoint surface.
- [WORK-ORDER](../agentx/WORK-ORDER-adls-gen2-lite-emulator.md) - acceptance criteria and hidden edge cases.
- [PRD](../product/PRD-adls-gen2-lite-emulator.md) - functional, SDK, and runtime requirements.
- Microsoft Learn: "Azure Data Lake Storage Gen2 REST API" reference, Path Create / Update / Read / Lease / Delete pages.
- `azure-sdk-for-python` repo: `sdk/storage/azure-storage-file-datalake` source - `_data_lake_file_client.py`, `_path_client.py`, `_file_system_client.py`, generated REST layer under `_generated/_operations/`.
