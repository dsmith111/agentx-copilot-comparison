"""Direct HTTP tests against the FastAPI emulator app."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from em_agentx.app import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.text == "OK"


def test_create_and_delete_filesystem_dfs_style(client):
    r = client.put("/devstoreaccount1/fs1?resource=filesystem")
    assert r.status_code == 201
    assert r.headers.get("etag")
    r = client.delete("/devstoreaccount1/fs1")
    assert r.status_code == 202


def test_create_filesystem_container_style(client):
    r = client.put("/devstoreaccount1/fsblob?restype=container")
    assert r.status_code == 201
    r = client.delete("/devstoreaccount1/fsblob?restype=container")
    assert r.status_code == 202


def test_path_lifecycle(client):
    client.put("/devstoreaccount1/fs2?resource=filesystem")
    r = client.put("/devstoreaccount1/fs2/dir1?resource=directory")
    assert r.status_code == 201
    r = client.put("/devstoreaccount1/fs2/dir1/x.txt?resource=file")
    assert r.status_code == 201

    payload = b"abcdefghij"
    r = client.patch(
        "/devstoreaccount1/fs2/dir1/x.txt?action=append&position=0",
        content=payload,
    )
    assert r.status_code == 202
    r = client.patch(
        f"/devstoreaccount1/fs2/dir1/x.txt?action=flush&position={len(payload)}"
    )
    assert r.status_code == 200

    r = client.get("/devstoreaccount1/fs2/dir1/x.txt")
    assert r.status_code == 200
    assert r.content == payload

    r = client.head("/devstoreaccount1/fs2/dir1/x.txt")
    assert r.status_code == 200
    assert r.headers["x-ms-resource-type"] == "file"
    assert r.headers["content-length"] == str(len(payload))

    r = client.get("/devstoreaccount1/fs2?resource=filesystem&recursive=true")
    assert r.status_code == 200
    names = sorted(p["name"] for p in r.json()["paths"])
    assert names == ["dir1", "dir1/x.txt"]


def test_range_read(client):
    client.put("/devstoreaccount1/fsr?resource=filesystem")
    client.put("/devstoreaccount1/fsr/f.txt?resource=file")
    client.patch(
        "/devstoreaccount1/fsr/f.txt?action=append&position=0", content=b"abcdef"
    )
    client.patch("/devstoreaccount1/fsr/f.txt?action=flush&position=6")

    r = client.get("/devstoreaccount1/fsr/f.txt", headers={"x-ms-range": "bytes=2-4"})
    assert r.status_code == 206
    assert r.content == b"cde"
    assert r.headers["content-range"] == "bytes 2-4/6"

    # Range that exceeds the file size should be clamped, not error.
    r = client.get("/devstoreaccount1/fsr/f.txt", headers={"x-ms-range": "bytes=0-9999"})
    assert r.status_code == 206
    assert r.content == b"abcdef"


def test_rename_via_header(client):
    client.put("/devstoreaccount1/fsm?resource=filesystem")
    client.put("/devstoreaccount1/fsm/old.txt?resource=file")
    client.patch(
        "/devstoreaccount1/fsm/old.txt?action=append&position=0", content=b"data"
    )
    client.patch("/devstoreaccount1/fsm/old.txt?action=flush&position=4")
    r = client.put(
        "/devstoreaccount1/fsm/new.txt",
        headers={"x-ms-rename-source": "/fsm/old.txt"},
    )
    assert r.status_code == 201
    r = client.get("/devstoreaccount1/fsm/new.txt")
    assert r.status_code == 200
    assert r.content == b"data"
    r = client.get("/devstoreaccount1/fsm/old.txt")
    assert r.status_code == 404


def test_delete_non_empty_directory_requires_recursive(client):
    client.put("/devstoreaccount1/fsd?resource=filesystem")
    client.put("/devstoreaccount1/fsd/d?resource=directory")
    client.put("/devstoreaccount1/fsd/d/x?resource=file")
    r = client.delete("/devstoreaccount1/fsd/d")
    assert r.status_code == 409
    r = client.delete("/devstoreaccount1/fsd/d?recursive=true")
    assert r.status_code == 200


def test_account_prefix_optional(client):
    # Without the account prefix the routes still work.
    r = client.put("/fsb?resource=filesystem")
    assert r.status_code == 201
    r = client.put("/fsb/x?resource=file")
    assert r.status_code == 201
