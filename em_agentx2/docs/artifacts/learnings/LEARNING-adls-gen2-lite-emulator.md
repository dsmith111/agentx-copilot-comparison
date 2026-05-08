# Learning: ADLS Gen2 Lite Emulator

**Issue:** adls-gen2-lite-emulator
**Date:** 2025-07-01
**Classification:** Mandatory (reusable SDK compatibility + emulator design patterns)

---

## 1. SDK Wire Shape Discoveries (azure-storage-file-datalake 12.23.0)

These SDK behaviours are not documented in the Azure REST API spec and were only
discoverable by running the SDK and observing request traffic.

### BUG-1: AzureNamedKeyCredential

`StorageSharedKeyCredential` was removed from `azure.storage.blob` in recent SDK
versions. Emulators must accept `AzureNamedKeyCredential` from `azure.core.credentials`
for Shared Key auth.

### BUG-2: Filesystem ops use ?restype=container

When creating or deleting a filesystem, the SDK sends:

```
PUT  /{account}/{filesystem}?restype=container
DELETE /{account}/{filesystem}?restype=container
```

Not `?resource=filesystem` as documented for the DFS API.  Both query-string forms
must be handled by the same handler.

### BUG-3: Flush response -- metadata only, empty body

After `PATCH ?action=flush`, the SDK expects:

- `Content-Length: 0` (or absent)
- ETag + Last-Modified headers
- No body bytes

If Content-Length reflects the file size, the SDK stalls waiting for bytes.

### BUG-4: Partial download uses x-ms-range, not Range

The SDK sends `x-ms-range: bytes=0-N` for partial reads.  Standard `Range: bytes=0-N`
is ignored.  The emulator must parse `x-ms-range` and produce a `Content-Range`
response header.

### BUG-5: Rename uses mode=legacy + x-ms-rename-source

The SDK sends:

```
PUT /{account}/{fs}/{new-path}?mode=legacy
x-ms-rename-source: /{account}/{fs}/{old-path}
```

Not `renameSource` query parameter, and not `mode=rename`.  The emulator must accept
all three mode variants (legacy, posix, rename) and the header form.  Rename response
must also have an empty body.

---

## 2. Emulator Design Patterns

### In-memory + snapshot dual-store

Use `InMemoryStore` in tests (fast, isolated) and `SnapshotStore` (extends InMemoryStore)
in Docker.  SnapshotStore adds atomic `tempfile -> os.replace()` persistence.  This
pattern avoids mocking and tests real application logic at unit speed.

### Per-filesystem asyncio locks

Create one `asyncio.Lock` per filesystem at creation time.  All mutating path
operations (rename, delete, flush) acquire the lock.  Create/append can be lock-free
at path level because they do not reorder nodes.

### Blob file cleanup on filesystem delete

When a filesystem is deleted, collect all `node.node_id` values before calling
`super().delete_filesystem()`, then remove each `{store_dir}/v1/blobs/{id}.bin`.
If the call order is reversed (super first), the node IDs are gone.

### 416 on range request against empty file

Return `HTTP 416 Range Not Satisfiable` with header `Content-Range: bytes */0` when:
- `x-ms-range` header is present
- file size is 0

Returning `Content-Range: bytes 0--1/0` is malformed per RFC 7233 and will confuse
SDK retry logic.

---

## 3. Test Strategy

- TestClient (ASGI in-process) for all API tests -- no network, no port conflicts.
- Real `azure-storage-file-datalake` SDK in smoke tests, skipped when emulator not
  running on port 10004 (CI-safe).
- Regression test for every SDK compatibility fix and every code-review MEDIUM+ finding.
