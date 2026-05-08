def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.text == "OK"


def test_filesystem_lifecycle(client):
    r = client.put("/devstoreaccount1/fs1", params={"resource": "filesystem"})
    assert r.status_code == 201
    r = client.put("/devstoreaccount1/fs1", params={"resource": "filesystem"})
    assert r.status_code == 409

    r = client.head("/devstoreaccount1/fs1")
    assert r.status_code == 200

    r = client.delete("/devstoreaccount1/fs1")
    assert r.status_code == 202

    r = client.head("/devstoreaccount1/fs1")
    assert r.status_code == 404


def test_blob_style_filesystem_create(client):
    # The DataLake SDK delegates create_file_system to the blob client which
    # uses ?restype=container instead of ?resource=filesystem.
    r = client.put("/devstoreaccount1/fs2", params={"restype": "container"})
    assert r.status_code == 201


def test_create_dir_file_append_flush_read(client):
    client.put("/devstoreaccount1/fs", params={"resource": "filesystem"})

    r = client.put(
        "/devstoreaccount1/fs/dir/sub", params={"resource": "directory"}
    )
    assert r.status_code == 201

    r = client.put("/devstoreaccount1/fs/dir/sub/file.txt", params={"resource": "file"})
    assert r.status_code == 201

    r = client.patch(
        "/devstoreaccount1/fs/dir/sub/file.txt",
        params={"action": "append", "position": 0},
        content=b"hello world",
    )
    assert r.status_code == 202

    r = client.patch(
        "/devstoreaccount1/fs/dir/sub/file.txt",
        params={"action": "flush", "position": 11},
    )
    assert r.status_code == 200

    r = client.get("/devstoreaccount1/fs/dir/sub/file.txt")
    assert r.status_code == 200
    assert r.content == b"hello world"
    assert r.headers["Content-Length"] == "11"
    assert r.headers["x-ms-resource-type"] == "file"


def test_range_read(client):
    client.put("/devstoreaccount1/fs", params={"resource": "filesystem"})
    client.put("/devstoreaccount1/fs/f", params={"resource": "file"})
    client.patch(
        "/devstoreaccount1/fs/f",
        params={"action": "append", "position": 0},
        content=b"abcdefghij",
    )
    client.patch("/devstoreaccount1/fs/f", params={"action": "flush", "position": 10})

    r = client.get("/devstoreaccount1/fs/f", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"cdef"
    assert r.headers["Content-Range"] == "bytes 2-5/10"


def test_list_paths(client):
    client.put("/devstoreaccount1/fs", params={"resource": "filesystem"})
    for p in ["a/b/c.txt", "a/b/d.txt", "a/e.txt", "top.txt"]:
        client.put(f"/devstoreaccount1/fs/{p}", params={"resource": "file"})

    r = client.get(
        "/devstoreaccount1/fs",
        params={"resource": "filesystem", "recursive": "true"},
    )
    assert r.status_code == 200
    names = sorted(p["name"] for p in r.json()["paths"])
    assert names == ["a", "a/b", "a/b/c.txt", "a/b/d.txt", "a/e.txt", "top.txt"]

    r = client.get(
        "/devstoreaccount1/fs",
        params={"resource": "filesystem", "recursive": "false", "directory": "a"},
    )
    names = sorted(p["name"] for p in r.json()["paths"])
    assert names == ["a/b", "a/e.txt"]


def test_rename_file(client):
    client.put("/devstoreaccount1/fs", params={"resource": "filesystem"})
    client.put("/devstoreaccount1/fs/old.txt", params={"resource": "file"})
    client.patch(
        "/devstoreaccount1/fs/old.txt",
        params={"action": "append", "position": 0},
        content=b"data",
    )
    client.patch(
        "/devstoreaccount1/fs/old.txt",
        params={"action": "flush", "position": 4},
    )

    r = client.put(
        "/devstoreaccount1/fs/new.txt",
        params={"mode": "legacy"},
        headers={"x-ms-rename-source": "/fs/old.txt"},
    )
    assert r.status_code == 201

    assert client.get("/devstoreaccount1/fs/old.txt").status_code == 404
    r = client.get("/devstoreaccount1/fs/new.txt")
    assert r.status_code == 200 and r.content == b"data"


def test_rename_file_in_nested_directory(client):
    """Regression: rename source paths must preserve all path segments.

    Previously the parser used ``str.split(maxsplit=2)`` which silently
    discarded the file name when the source was nested (e.g.
    ``/fs/a/b/c.txt``), causing the rename to operate on the wrong node and
    subsequent reads of the destination to return an empty file.
    """
    client.put("/devstoreaccount1/fs", params={"resource": "filesystem"})
    client.put("/devstoreaccount1/fs/dir/sub/old.txt", params={"resource": "file"})
    client.patch(
        "/devstoreaccount1/fs/dir/sub/old.txt",
        params={"action": "append", "position": 0},
        content=b"hello world!",
    )
    client.patch(
        "/devstoreaccount1/fs/dir/sub/old.txt",
        params={"action": "flush", "position": 12},
    )

    r = client.put(
        "/devstoreaccount1/fs/dir/sub/new.txt",
        params={"mode": "legacy"},
        headers={"x-ms-rename-source": "/fs/dir/sub/old.txt"},
    )
    assert r.status_code == 201, r.text

    r = client.get("/devstoreaccount1/fs/dir/sub/new.txt")
    assert r.status_code == 200
    assert r.content == b"hello world!"
    assert client.head("/devstoreaccount1/fs/dir/sub/old.txt").status_code == 404


def test_rename_source_with_account_prefix(client):
    """The SDK can include the account name in x-ms-rename-source."""
    client.put("/devstoreaccount1/fs", params={"resource": "filesystem"})
    client.put("/devstoreaccount1/fs/a/b/c.txt", params={"resource": "file"})

    r = client.put(
        "/devstoreaccount1/fs/a/b/d.txt",
        params={"mode": "legacy"},
        headers={"x-ms-rename-source": "/devstoreaccount1/fs/a/b/c.txt"},
    )
    assert r.status_code == 201, r.text
    assert client.head("/devstoreaccount1/fs/a/b/d.txt").status_code == 200
    assert client.head("/devstoreaccount1/fs/a/b/c.txt").status_code == 404


def test_delete_directory_recursive(client):
    client.put("/devstoreaccount1/fs", params={"resource": "filesystem"})
    client.put("/devstoreaccount1/fs/d/f", params={"resource": "file"})

    r = client.delete("/devstoreaccount1/fs/d", params={"recursive": "false"})
    assert r.status_code == 409

    r = client.delete("/devstoreaccount1/fs/d", params={"recursive": "true"})
    assert r.status_code == 200
    assert client.head("/devstoreaccount1/fs/d/f").status_code == 404


def test_path_without_account_prefix(client):
    # Must tolerate either "/{account}/..." or "/..." paths.
    r = client.put("/fs-noacc", params={"resource": "filesystem"})
    assert r.status_code == 201
    r = client.put("/fs-noacc/file", params={"resource": "file"})
    assert r.status_code == 201


def test_append_invalid_position(client):
    client.put("/devstoreaccount1/fs", params={"resource": "filesystem"})
    client.put("/devstoreaccount1/fs/f", params={"resource": "file"})
    r = client.patch(
        "/devstoreaccount1/fs/f",
        params={"action": "append", "position": 5},
        content=b"x",
    )
    assert r.status_code == 400


def test_404_on_missing(client):
    r = client.get("/devstoreaccount1/missing-fs/anything")
    assert r.status_code == 404
