"""Direct HTTP API tests using Starlette TestClient (ASGI in-process)."""
import pytest
from starlette.testclient import TestClient
from adls_lite.app import create_app
from adls_lite.store.memory import InMemoryStore


@pytest.fixture()
def client():
    store = InMemoryStore()
    app = create_app(store=store)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.text == "OK"


def test_health_has_ms_version(client):
    r = client.get("/health")
    assert "x-ms-version" in r.headers


# ---------------------------------------------------------------------------
# Filesystem CRUD
# ---------------------------------------------------------------------------

def test_create_filesystem(client):
    r = client.put("/devstoreaccount1/myfs?resource=filesystem")
    assert r.status_code == 201


def test_create_filesystem_duplicate_returns_409(client):
    client.put("/devstoreaccount1/myfs?resource=filesystem")
    r = client.put("/devstoreaccount1/myfs?resource=filesystem")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "FilesystemAlreadyExists"


def test_create_delete_filesystem_blob_container_style(client):
    r = client.put("/devstoreaccount1/myfs?restype=container")
    assert r.status_code == 201

    r = client.delete("/devstoreaccount1/myfs?restype=container")
    assert r.status_code == 202


def test_delete_filesystem(client):
    client.put("/devstoreaccount1/myfs?resource=filesystem")
    r = client.delete("/devstoreaccount1/myfs")
    assert r.status_code == 202


def test_delete_missing_filesystem_returns_404(client):
    r = client.delete("/devstoreaccount1/ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "FilesystemNotFound"


def test_list_filesystems(client):
    client.put("/devstoreaccount1/fs1?resource=filesystem")
    client.put("/devstoreaccount1/fs2?resource=filesystem")
    r = client.get("/devstoreaccount1?resource=account&comp=list")
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["filesystems"]]
    assert "fs1" in names and "fs2" in names


# ---------------------------------------------------------------------------
# Directory and file creation
# ---------------------------------------------------------------------------

def _setup_fs(client, name="myfs"):
    client.put(f"/devstoreaccount1/{name}?resource=filesystem")


def test_create_directory(client):
    _setup_fs(client)
    r = client.put("/devstoreaccount1/myfs/dir1?resource=directory")
    assert r.status_code == 201
    assert r.headers["x-ms-resource-type"] == "directory"


def test_create_file(client):
    _setup_fs(client)
    r = client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    assert r.status_code == 201
    assert r.headers["x-ms-resource-type"] == "file"


def test_create_file_if_none_match_star_duplicate_returns_409(client):
    """EC-3: If-None-Match: * on existing file must fail."""
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    r = client.put(
        "/devstoreaccount1/myfs/file.txt?resource=file",
        headers={"If-None-Match": "*"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PathAlreadyExists"


def test_create_file_duplicate_without_header_returns_409(client):
    """EC-3 regression: duplicate create_file() with no If-None-Match header must also fail.

    DataLakeFileClient.create_file() in SDK 12.x does not send If-None-Match: *
    by default, so the emulator must reject duplicates unconditionally.
    """
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    r = client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PathAlreadyExists"


def test_create_child_under_file_parent_fails(client):
    """EC-2: Child path under file parent must fail."""
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    r = client.put("/devstoreaccount1/myfs/file.txt/child?resource=file")
    assert r.status_code in (409, 400)


# ---------------------------------------------------------------------------
# Append and flush
# ---------------------------------------------------------------------------

def test_append_and_flush(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    r = client.patch(
        "/devstoreaccount1/myfs/file.txt?action=append&position=0",
        content=b"hello",
    )
    assert r.status_code == 202
    r = client.patch("/devstoreaccount1/myfs/file.txt?action=flush&position=5")
    assert r.status_code == 200


def test_flush_response_has_empty_body_metadata_only(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    client.patch(
        "/devstoreaccount1/myfs/file.txt?action=append&position=0",
        content=b"hello",
    )
    r = client.patch("/devstoreaccount1/myfs/file.txt?action=flush&position=5")
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers.get("Content-Length") in (None, "0")
    assert "ETag" in r.headers
    assert "Last-Modified" in r.headers


def test_append_wrong_position_returns_400(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    r = client.patch(
        "/devstoreaccount1/myfs/file.txt?action=append&position=99",
        content=b"data",
    )
    assert r.status_code == 400


def test_flush_wrong_position_returns_400(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    client.patch(
        "/devstoreaccount1/myfs/file.txt?action=append&position=0",
        content=b"hello",
    )
    r = client.patch("/devstoreaccount1/myfs/file.txt?action=flush&position=100")
    assert r.status_code == 400


def test_repeated_append_flush(client):
    """EC-1: Repeated append/flush extends content."""
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    client.patch("/devstoreaccount1/myfs/file.txt?action=append&position=0",
                 content=b"hello")
    client.patch("/devstoreaccount1/myfs/file.txt?action=flush&position=5")
    client.patch("/devstoreaccount1/myfs/file.txt?action=append&position=5",
                 content=b" world")
    client.patch("/devstoreaccount1/myfs/file.txt?action=flush&position=11")
    r = client.get("/devstoreaccount1/myfs/file.txt")
    assert r.status_code == 200
    assert r.content == b"hello world"


# ---------------------------------------------------------------------------
# Read and HEAD
# ---------------------------------------------------------------------------

def test_get_file_returns_bytes(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    client.patch("/devstoreaccount1/myfs/file.txt?action=append&position=0",
                 content=b"payload")
    client.patch("/devstoreaccount1/myfs/file.txt?action=flush&position=7")
    r = client.get("/devstoreaccount1/myfs/file.txt")
    assert r.status_code == 200
    assert r.content == b"payload"


def test_get_file_honors_x_ms_range_header(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    client.patch(
        "/devstoreaccount1/myfs/file.txt?action=append&position=0",
        content=b"payload",
    )
    client.patch("/devstoreaccount1/myfs/file.txt?action=flush&position=7")
    r = client.get(
        "/devstoreaccount1/myfs/file.txt",
        headers={"x-ms-range": "bytes=0-3"},
    )
    assert r.status_code == 206
    assert r.content == b"payl"
    assert r.headers["Content-Range"] == "bytes 0-3/7"
    assert r.headers["Content-Length"] == "4"


def test_head_file_returns_properties(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    client.patch("/devstoreaccount1/myfs/file.txt?action=append&position=0",
                 content=b"abc")
    client.patch("/devstoreaccount1/myfs/file.txt?action=flush&position=3")
    r = client.head("/devstoreaccount1/myfs/file.txt")
    assert r.status_code == 200
    assert r.headers["Content-Length"] == "3"
    assert r.headers["x-ms-resource-type"] == "file"
    assert "ETag" in r.headers
    assert "Last-Modified" in r.headers


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_list_paths_recursive(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/dir1?resource=directory")
    client.put("/devstoreaccount1/myfs/dir1/file.txt?resource=file")
    client.patch("/devstoreaccount1/myfs/dir1/file.txt?action=append&position=0",
                 content=b"x")
    client.patch("/devstoreaccount1/myfs/dir1/file.txt?action=flush&position=1")
    r = client.get("/devstoreaccount1/myfs?resource=filesystem&recursive=true")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["paths"]]
    assert "dir1" in names
    assert "dir1/file.txt" in names


def test_list_paths_non_recursive(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/dir1?resource=directory")
    client.put("/devstoreaccount1/myfs/dir1/nested?resource=directory")
    r = client.get("/devstoreaccount1/myfs?resource=filesystem&recursive=false")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["paths"]]
    assert "dir1" in names
    assert "dir1/nested" not in names


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

def test_rename_using_header(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/old.txt?resource=file")
    client.patch("/devstoreaccount1/myfs/old.txt?action=append&position=0",
                 content=b"data")
    client.patch("/devstoreaccount1/myfs/old.txt?action=flush&position=4")
    r = client.put(
        "/devstoreaccount1/myfs/new.txt?mode=rename",
        headers={"x-ms-rename-source": "/devstoreaccount1/myfs/old.txt"},
    )
    assert r.status_code == 201


def test_rename_old_path_missing_after_success(client):
    """EC-6: Old path must be 404 after rename."""
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/old.txt?resource=file")
    client.put(
        "/devstoreaccount1/myfs/new.txt?mode=rename",
        headers={"x-ms-rename-source": "/devstoreaccount1/myfs/old.txt"},
    )
    r = client.get("/devstoreaccount1/myfs/old.txt")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PathNotFound"


def test_rename_using_query_param(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/old.txt?resource=file")
    r = client.put(
        "/devstoreaccount1/myfs/new.txt?mode=rename&renameSource=/devstoreaccount1/myfs/old.txt"
    )
    assert r.status_code == 201


def test_rename_using_legacy_mode_has_empty_body_metadata_only(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/old.txt?resource=file")
    client.patch(
        "/devstoreaccount1/myfs/old.txt?action=append&position=0",
        content=b"data",
    )
    client.patch("/devstoreaccount1/myfs/old.txt?action=flush&position=4")
    r = client.put(
        "/devstoreaccount1/myfs/new.txt?mode=legacy",
        headers={"x-ms-rename-source": "/devstoreaccount1/myfs/old.txt"},
    )
    assert r.status_code == 201
    assert r.content == b""
    assert r.headers.get("Content-Length") in (None, "0")
    assert "ETag" in r.headers
    assert "Last-Modified" in r.headers

    r = client.get("/devstoreaccount1/myfs/new.txt")
    assert r.status_code == 200
    assert r.content == b"data"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_file(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    r = client.delete("/devstoreaccount1/myfs/file.txt")
    assert r.status_code == 200


def test_delete_missing_path_returns_404(client):
    _setup_fs(client)
    r = client.delete("/devstoreaccount1/myfs/ghost.txt")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PathNotFound"


def test_delete_non_empty_dir_without_recursive_returns_409(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/dir1?resource=directory")
    client.put("/devstoreaccount1/myfs/dir1/file.txt?resource=file")
    r = client.delete("/devstoreaccount1/myfs/dir1?recursive=false")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "DirectoryNotEmpty"


def test_get_deleted_file_returns_404(client):
    """EC-4: Deleted file GET must return 404 PathNotFound."""
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    client.delete("/devstoreaccount1/myfs/file.txt")
    r = client.get("/devstoreaccount1/myfs/file.txt")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PathNotFound"


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------

def test_every_response_has_ms_request_id(client):
    r = client.get("/health")
    assert "x-ms-request-id" in r.headers


def test_error_response_has_error_envelope(client):
    r = client.delete("/devstoreaccount1/ghost")
    data = r.json()
    assert "error" in data
    assert "code" in data["error"]
    assert "message" in data["error"]


def test_out_of_scope_endpoint_returns_501(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/file.txt?resource=file")
    r = client.put(
        "/devstoreaccount1/myfs/file.txt?comp=lease",
    )
    assert r.status_code == 501


# ---------------------------------------------------------------------------
# Range on empty file — regression (x-ms-range on zero-byte file must 416)
# ---------------------------------------------------------------------------

def test_get_empty_file_with_range_returns_416(client):
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/empty.txt?resource=file")
    r = client.get(
        "/devstoreaccount1/myfs/empty.txt",
        headers={"x-ms-range": "bytes=0-33554431"},
    )
    assert r.status_code == 416
    assert r.headers.get("Content-Range") == "bytes */0"


def test_get_file_range_start_past_eof_returns_416(client):
    """MAJOR-1 regression: x-ms-range start >= file size must 416, not malformed 206."""
    _setup_fs(client)
    client.put("/devstoreaccount1/myfs/f.txt?resource=file")
    client.patch("/devstoreaccount1/myfs/f.txt?action=append&position=0", content=b"abc")
    client.patch("/devstoreaccount1/myfs/f.txt?action=flush&position=3")
    r = client.get(
        "/devstoreaccount1/myfs/f.txt",
        headers={"x-ms-range": "bytes=10-20"},
    )
    assert r.status_code == 416
    assert r.headers.get("Content-Range") == "bytes */3"
