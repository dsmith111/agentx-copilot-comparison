# ADLS Gen2 Lite Emulator

## Goal

Build a Dockerized local emulator for a practical subset of Azure Data Lake Storage Gen2.

The emulator must allow a developer to point the real Azure Python SDK at a local endpoint and run basic filesystem, directory, and file workflows without using Azure.

This is not a full Azure Storage clone. It is a deterministic local emulator subset for integration tests.

## Non-goals

Do not implement:
- Azure Blob API
- Queue or Table storage
- Azure Files
- OAuth / Microsoft Entra ID
- ACL enforcement
- leases
- soft delete
- encryption scopes
- billing/account management
- perfect Azure error parity
- Delta Lake transaction protocol

## Required runtime

The final project must provide:

- Dockerfile
- docker-compose.yml
- local server listening on port 10004 by default
- persistent data volume
- in-memory mode for tests
- README with setup and SDK usage
- automated tests
- examples/python_sdk_smoke.py

## API compatibility target

Implement enough of the Azure Data Lake Storage Gen2 filesystem/path REST shape for the Azure Python SDK to perform:

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

The server may accept any Authorization header. It does not need to verify SharedKey signatures.

## Data model

Use a hierarchical namespace.

Each account has filesystems.
Each filesystem has paths.
A path is either:
- directory
- file

Directories are real nodes.
Files store bytes.
Renames of directories must be atomic at emulator level.

## Endpoints

Support local account-style routes.

Example base URL:

http://127.0.0.1:10004/devstoreaccount1

The implementation should tolerate either:
- /{account}/{filesystem}/{path}
- /{filesystem}/{path}

depending on what the SDK sends.

Implement request handlers for:

- PUT /{filesystem}?resource=filesystem
- DELETE /{filesystem}
- GET /?resource=account
- PUT /{filesystem}/{path}?resource=directory
- PUT /{filesystem}/{path}?resource=file
- PATCH /{filesystem}/{path}?action=append&position=N
- PATCH /{filesystem}/{path}?action=flush&position=N
- GET /{filesystem}/{path}
- HEAD /{filesystem}/{path}
- GET /{filesystem}?resource=filesystem&recursive=true|false&directory=...
- PUT /{filesystem}/{path}?mode=rename&renameSource=...
- DELETE /{filesystem}/{path}?recursive=true|false

If the Azure SDK sends slightly different query strings, adapt to the SDK behavior while preserving tests.

## Error behavior

Return sensible HTTP errors:

- 404 for missing filesystem/path
- 409 for conflicts where overwrite is false
- 400 for malformed append/flush positions
- 409 for deleting non-empty directory without recursive=true

Exact Azure error XML is optional for MVP, but responses should include useful error text.

## Persistence

Support two modes:

- in-memory mode for unit tests
- local filesystem persistence for Docker

Persistence layout is implementation-defined, but must survive container restart when using the Docker volume.

## Tests

Required tests:

1. Unit tests for filesystem/path store.
2. API tests using direct HTTP calls.
3. SDK smoke test using azure-storage-file-datalake.
4. Docker smoke test.

The SDK smoke test must:
- create a filesystem
- create a directory
- upload a file by create/append/flush
- read it back
- list paths
- rename it
- delete it

## Acceptance criteria

The project is complete when:

- `docker compose up` starts the emulator
- `python examples/python_sdk_smoke.py` passes against the running emulator
- `pytest -q` passes
- `scripts/evaluate.sh` passes
- README explains SDK configuration
- No live Azure resource is required

## Preferred implementation

Use Python + FastAPI or Node.js + TypeScript.

Prefer clarity and testability over perfect API parity.

## Reproducibility

The implementation must be deterministic:
- stable ports
- stable account name
- stable test data
- no external network calls except package installation
- tests must clean up after themselves