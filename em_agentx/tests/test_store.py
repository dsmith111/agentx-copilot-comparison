"""Unit tests for the in-memory Store data model."""
from __future__ import annotations

import pytest

from em_agentx.store import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    Store,
    normalize_path,
)


def test_normalize_path():
    assert normalize_path("") == ""
    assert normalize_path("/") == ""
    assert normalize_path("a") == "a"
    assert normalize_path("/a/b/") == "a/b"
    assert normalize_path("a//b///c") == "a/b/c"


def test_create_filesystem_idempotent():
    s = Store()
    s.create_filesystem("fs1")
    with pytest.raises(ConflictError):
        s.create_filesystem("fs1")


def test_delete_unknown_filesystem_raises():
    s = Store()
    with pytest.raises(NotFoundError):
        s.delete_filesystem("nope")


def test_create_directory_creates_parents():
    s = Store()
    s.create_filesystem("fs1")
    s.create_directory("fs1", "a/b/c")
    names = sorted(p.name for p in s.iter_paths("fs1"))
    assert names == ["a", "a/b", "a/b/c"]


def test_create_directory_idempotent():
    s = Store()
    s.create_filesystem("fs1")
    e1 = s.create_directory("fs1", "d")
    e2 = s.create_directory("fs1", "d")
    assert e1.name == e2.name == "d"
    assert e1.is_directory


def test_create_file_after_directory_conflicts():
    s = Store()
    s.create_filesystem("fs1")
    s.create_directory("fs1", "d")
    with pytest.raises(ConflictError):
        s.create_file("fs1", "d")


def test_append_flush_read_cycle():
    s = Store()
    s.create_filesystem("fs1")
    s.create_file("fs1", "f.txt")
    s.append("fs1", "f.txt", 0, b"hello ")
    s.append("fs1", "f.txt", 6, b"world")
    s.flush("fs1", "f.txt", 11)
    content, entry = s.read("fs1", "f.txt")
    assert content == b"hello world"
    assert entry.content_size == 11


def test_append_invalid_position():
    s = Store()
    s.create_filesystem("fs1")
    s.create_file("fs1", "f.txt")
    with pytest.raises(BadRequestError):
        s.append("fs1", "f.txt", 5, b"oops")


def test_flush_invalid_position():
    s = Store()
    s.create_filesystem("fs1")
    s.create_file("fs1", "f.txt")
    s.append("fs1", "f.txt", 0, b"abc")
    with pytest.raises(BadRequestError):
        s.flush("fs1", "f.txt", 99)


def test_overwrite_file_resets_content():
    s = Store()
    s.create_filesystem("fs1")
    s.create_file("fs1", "f.txt")
    s.append("fs1", "f.txt", 0, b"old")
    s.flush("fs1", "f.txt", 3)
    s.create_file("fs1", "f.txt", overwrite=True)
    content, entry = s.read("fs1", "f.txt")
    assert content == b""
    assert entry.content_size == 0


def test_list_paths_recursive_and_flat():
    s = Store()
    s.create_filesystem("fs1")
    s.create_directory("fs1", "a/b")
    s.create_file("fs1", "a/b/file.txt")
    s.create_file("fs1", "a/top.txt")
    flat = [p.name for p in s.list_paths("fs1", recursive=False)]
    assert flat == ["a"]
    deep = sorted(p.name for p in s.list_paths("fs1", recursive=True))
    assert deep == ["a", "a/b", "a/b/file.txt", "a/top.txt"]
    sub = sorted(p.name for p in s.list_paths("fs1", recursive=True, directory="a"))
    assert sub == ["a/b", "a/b/file.txt", "a/top.txt"]


def test_rename_file():
    s = Store()
    s.create_filesystem("fs1")
    s.create_file("fs1", "old.txt")
    s.append("fs1", "old.txt", 0, b"data")
    s.flush("fs1", "old.txt", 4)
    s.rename("fs1", "old.txt", "fs1", "renamed/new.txt")
    content, _ = s.read("fs1", "renamed/new.txt")
    assert content == b"data"
    with pytest.raises(NotFoundError):
        s.read("fs1", "old.txt")


def test_rename_directory_recursively():
    s = Store()
    s.create_filesystem("fs1")
    s.create_directory("fs1", "src/inner")
    s.create_file("fs1", "src/inner/leaf.txt")
    s.append("fs1", "src/inner/leaf.txt", 0, b"x")
    s.flush("fs1", "src/inner/leaf.txt", 1)
    s.rename("fs1", "src", "fs1", "moved")
    names = sorted(p.name for p in s.list_paths("fs1", recursive=True))
    assert names == ["moved", "moved/inner", "moved/inner/leaf.txt"]
    content, _ = s.read("fs1", "moved/inner/leaf.txt")
    assert content == b"x"


def test_delete_directory_requires_recursive():
    s = Store()
    s.create_filesystem("fs1")
    s.create_directory("fs1", "d")
    s.create_file("fs1", "d/x")
    with pytest.raises(ConflictError):
        s.delete("fs1", "d", recursive=False)
    s.delete("fs1", "d", recursive=True)
    assert sorted(p.name for p in s.iter_paths("fs1")) == []


def test_delete_filesystem_removes_all():
    s = Store()
    s.create_filesystem("fs1")
    s.create_file("fs1", "x")
    s.delete_filesystem("fs1")
    with pytest.raises(NotFoundError):
        s.get_filesystem("fs1")


def test_disk_persistence_roundtrip(tmp_path):
    s1 = Store(data_dir=tmp_path)
    s1.create_filesystem("fs1")
    s1.create_directory("fs1", "d")
    s1.create_file("fs1", "d/x.txt")
    s1.append("fs1", "d/x.txt", 0, b"persisted")
    s1.flush("fs1", "d/x.txt", 9)

    s2 = Store(data_dir=tmp_path)
    content, entry = s2.read("fs1", "d/x.txt")
    assert content == b"persisted"
    assert entry.content_size == 9
    names = sorted(p.name for p in s2.list_paths("fs1", recursive=True))
    assert names == ["d", "d/x.txt"]
