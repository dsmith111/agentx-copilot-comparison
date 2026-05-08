from adls_lite.store import Store


def test_create_and_list_filesystems():
    s = Store()
    assert s.create_filesystem("fs1") is True
    assert s.create_filesystem("fs1") is False
    assert s.create_filesystem("fs2") is True
    assert s.list_filesystems() == ["fs1", "fs2"]
    assert s.has_filesystem("fs1")


def test_delete_filesystem():
    s = Store()
    s.create_filesystem("fs1")
    assert s.delete_filesystem("fs1") is True
    assert s.delete_filesystem("fs1") is False
    assert s.list_filesystems() == []


def test_create_directory_and_file():
    s = Store()
    s.create_filesystem("fs")
    ok, n, err = s.create_directory("fs", "a/b/c")
    assert ok and n.is_directory and err == ""
    # Implicit parents created
    assert s.get_node("fs", "a") and s.get_node("fs", "a").is_directory
    assert s.get_node("fs", "a/b").is_directory

    ok, n, err = s.create_file("fs", "a/b/c/file.txt")
    assert ok and not n.is_directory


def test_append_flush_read_cycle():
    s = Store()
    s.create_filesystem("fs")
    s.create_file("fs", "data.bin")

    ok, err = s.append("fs", "data.bin", 0, b"hello ")
    assert ok, err
    ok, err = s.append("fs", "data.bin", 6, b"world")
    assert ok, err

    # Wrong position rejected
    ok, err = s.append("fs", "data.bin", 0, b"x")
    assert not ok and "position" in err

    ok, err = s.flush("fs", "data.bin", 11)
    assert ok, err

    assert s.read("fs", "data.bin") == b"hello world"
    assert s.read("fs", "data.bin", 0, 4) == b"hello"
    assert s.read("fs", "data.bin", 6) == b"world"


def test_flush_truncates_to_position():
    s = Store()
    s.create_filesystem("fs")
    s.create_file("fs", "f")
    s.append("fs", "f", 0, b"abcdef")
    ok, _ = s.flush("fs", "f", 3)
    assert ok
    assert s.read("fs", "f") == b"abc"


def test_list_paths_recursive_and_flat():
    s = Store()
    s.create_filesystem("fs")
    s.create_directory("fs", "dir1")
    s.create_file("fs", "dir1/file1.txt")
    s.create_directory("fs", "dir1/sub")
    s.create_file("fs", "dir1/sub/file2.txt")
    s.create_file("fs", "top.txt")

    flat = s.list_paths("fs", recursive=False)
    names = [p for p, _ in flat]
    assert "dir1" in names and "top.txt" in names
    assert all("/" not in p for p, _ in flat)

    recursive = s.list_paths("fs", recursive=True)
    names = sorted(p for p, _ in recursive)
    assert names == ["dir1", "dir1/file1.txt", "dir1/sub", "dir1/sub/file2.txt", "top.txt"]

    sub = s.list_paths("fs", directory="dir1", recursive=False)
    names = sorted(p for p, _ in sub)
    assert names == ["dir1/file1.txt", "dir1/sub"]


def test_delete_directory_requires_recursive():
    s = Store()
    s.create_filesystem("fs")
    s.create_directory("fs", "d")
    s.create_file("fs", "d/f")
    ok, err = s.delete("fs", "d", recursive=False)
    assert not ok and "not empty" in err
    ok, err = s.delete("fs", "d", recursive=True)
    assert ok
    assert s.get_node("fs", "d") is None
    assert s.get_node("fs", "d/f") is None


def test_rename_file():
    s = Store()
    s.create_filesystem("fs")
    s.create_file("fs", "a.txt")
    s.append("fs", "a.txt", 0, b"hi")
    s.flush("fs", "a.txt", 2)
    ok, err = s.rename("fs", "a.txt", "b.txt")
    assert ok, err
    assert s.get_node("fs", "a.txt") is None
    n = s.get_node("fs", "b.txt")
    assert n and bytes(n.data) == b"hi"


def test_rename_directory_moves_descendants():
    s = Store()
    s.create_filesystem("fs")
    s.create_directory("fs", "olddir")
    s.create_file("fs", "olddir/x")
    s.create_file("fs", "olddir/sub/y")
    ok, err = s.rename("fs", "olddir", "newdir")
    assert ok, err
    assert s.get_node("fs", "newdir/x") is not None
    assert s.get_node("fs", "newdir/sub/y") is not None
    assert s.get_node("fs", "olddir/x") is None


def test_persistence_round_trip(tmp_path):
    s = Store(root=str(tmp_path))
    s.create_filesystem("fs")
    s.create_file("fs", "f.bin")
    s.append("fs", "f.bin", 0, b"persist-me")
    s.flush("fs", "f.bin", 10)

    s2 = Store(root=str(tmp_path))
    assert s2.has_filesystem("fs")
    assert s2.read("fs", "f.bin") == b"persist-me"
