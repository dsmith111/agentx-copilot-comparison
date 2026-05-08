"""Unit tests for InMemoryStore invariants (no HTTP)."""
import pytest
from adls_lite.store.memory import InMemoryStore
from adls_lite.store.base import (
    FileNode, DirNode,
    FilesystemAlreadyExistsError, FilesystemNotFoundError,
    PathAlreadyExistsError, PathNotFoundError, PathConflictError,
    DirectoryNotEmptyError, InvalidRangeError, InvalidInputError,
)


@pytest.fixture()
def store():
    return InMemoryStore()


@pytest.fixture()
def fs(store):
    store.create_filesystem("testfs")
    return store


# ---------------------------------------------------------------------------
# Filesystem lifecycle
# ---------------------------------------------------------------------------

def test_create_filesystem(store):
    fs = store.create_filesystem("myfs")
    assert fs.name == "myfs"


def test_duplicate_filesystem_raises(store):
    store.create_filesystem("myfs")
    with pytest.raises(FilesystemAlreadyExistsError):
        store.create_filesystem("myfs")


def test_delete_filesystem(store):
    store.create_filesystem("myfs")
    store.delete_filesystem("myfs")
    with pytest.raises(FilesystemNotFoundError):
        store.get_filesystem("myfs")


def test_delete_missing_filesystem_raises(store):
    with pytest.raises(FilesystemNotFoundError):
        store.delete_filesystem("ghost")


def test_list_filesystems(store):
    store.create_filesystem("a")
    store.create_filesystem("b")
    names = {fs.name for fs in store.list_filesystems()}
    assert names == {"a", "b"}


# ---------------------------------------------------------------------------
# Directory and file creation
# ---------------------------------------------------------------------------

def test_create_directory(fs):
    node = fs.create_directory("testfs", "dir1")
    assert isinstance(node, DirNode)


def test_create_nested_directory(fs):
    fs.create_directory("testfs", "dir1")
    node = fs.create_directory("testfs", "dir1/subdir")
    assert isinstance(node, DirNode)


def test_create_directory_duplicate_raises(fs):
    fs.create_directory("testfs", "dir1")
    with pytest.raises(PathAlreadyExistsError):
        fs.create_directory("testfs", "dir1")


def test_create_file(fs):
    node = fs.create_file("testfs", "file.txt")
    assert isinstance(node, FileNode)
    assert node.committed == b""


def test_create_file_if_none_match_star_raises_when_exists(fs):
    fs.create_file("testfs", "file.txt")
    with pytest.raises(PathAlreadyExistsError):
        fs.create_file("testfs", "file.txt", if_none_match_star=True)


def test_create_file_without_flag_overwrites_when_exists(fs):
    fs.create_file("testfs", "file.txt")
    fs.append("testfs", "file.txt", 0, b"data")
    fs.flush("testfs", "file.txt", 4)
    # Re-create without flag should truncate
    fs.create_file("testfs", "file.txt", if_none_match_star=False)
    data = fs.read_file("testfs", "file.txt")
    assert data == b""


def test_child_path_under_file_parent_fails(fs):
    """EC-2: Creating a child path under a file MUST fail."""
    fs.create_file("testfs", "file.txt")
    with pytest.raises((PathConflictError, InvalidInputError)):
        fs.create_file("testfs", "file.txt/child.txt")


def test_path_traversal_rejected(fs):
    """Security: .. segments must be rejected."""
    with pytest.raises(InvalidInputError):
        fs.create_file("testfs", "dir/../etc/passwd")


# ---------------------------------------------------------------------------
# Append and flush
# ---------------------------------------------------------------------------

def test_append_and_flush_single_chunk(fs):
    fs.create_file("testfs", "file.txt")
    fs.append("testfs", "file.txt", 0, b"hello")
    node = fs.flush("testfs", "file.txt", 5)
    assert node.committed == b"hello"
    assert node.content_length == 5


def test_repeated_append_flush(fs):
    """EC-1: Multiple append/flush cycles extend the file."""
    fs.create_file("testfs", "file.txt")
    fs.append("testfs", "file.txt", 0, b"hello")
    fs.flush("testfs", "file.txt", 5)
    fs.append("testfs", "file.txt", 5, b" world")
    fs.flush("testfs", "file.txt", 11)
    assert fs.read_file("testfs", "file.txt") == b"hello world"


def test_multi_chunk_append_then_flush(fs):
    """EC-1: Multiple chunks in one flush cycle."""
    fs.create_file("testfs", "file.txt")
    fs.append("testfs", "file.txt", 0, b"abc")
    fs.append("testfs", "file.txt", 3, b"def")
    node = fs.flush("testfs", "file.txt", 6)
    assert node.committed == b"abcdef"


def test_append_wrong_position_raises(fs):
    fs.create_file("testfs", "file.txt")
    with pytest.raises(InvalidRangeError):
        fs.append("testfs", "file.txt", 5, b"data")  # wrong: file is empty


def test_flush_wrong_position_raises(fs):
    fs.create_file("testfs", "file.txt")
    fs.append("testfs", "file.txt", 0, b"hello")
    with pytest.raises(InvalidRangeError):
        fs.flush("testfs", "file.txt", 10)  # wrong: only 5 bytes appended


def test_flush_clears_uncommitted(fs):
    fs.create_file("testfs", "file.txt")
    fs.append("testfs", "file.txt", 0, b"data")
    fs.flush("testfs", "file.txt", 4)
    node = fs.get_node("testfs", "file.txt")
    assert node.uncommitted == b""


def test_read_returns_only_committed(fs):
    """Unflushed bytes must not be readable."""
    fs.create_file("testfs", "file.txt")
    fs.append("testfs", "file.txt", 0, b"pending")
    data = fs.read_file("testfs", "file.txt")
    assert data == b""


# ---------------------------------------------------------------------------
# List paths
# ---------------------------------------------------------------------------

def test_list_paths_recursive(fs):
    fs.create_directory("testfs", "dir1")
    fs.create_file("testfs", "dir1/file.txt")
    fs.append("testfs", "dir1/file.txt", 0, b"x")
    fs.flush("testfs", "dir1/file.txt", 1)
    paths = fs.list_paths("testfs", recursive=True)
    names = [p["name"] for p in paths]
    assert "dir1" in names
    assert "dir1/file.txt" in names


def test_list_paths_non_recursive(fs):
    fs.create_directory("testfs", "dir1")
    fs.create_directory("testfs", "dir1/sub")
    paths = fs.list_paths("testfs", recursive=False)
    names = [p["name"] for p in paths]
    assert "dir1" in names
    assert "dir1/sub" not in names


def test_list_paths_with_directory_filter(fs):
    fs.create_directory("testfs", "dir1")
    fs.create_file("testfs", "dir1/file.txt")
    fs.create_file("testfs", "top.txt")
    paths = fs.list_paths("testfs", recursive=True, directory="dir1")
    names = [p["name"] for p in paths]
    assert "dir1/file.txt" in names
    assert "top.txt" not in names


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

def test_rename_file(fs):
    fs.create_file("testfs", "old.txt")
    fs.append("testfs", "old.txt", 0, b"content")
    fs.flush("testfs", "old.txt", 7)
    fs.rename("testfs", "old.txt", "new.txt")
    # EC-6: old path must 404
    with pytest.raises(PathNotFoundError):
        fs.get_node("testfs", "old.txt")
    # New path serves the content
    assert fs.read_file("testfs", "new.txt") == b"content"


def test_rename_directory_moves_subtree(fs):
    fs.create_directory("testfs", "src")
    fs.create_file("testfs", "src/file.txt")
    fs.rename("testfs", "src", "dst")
    with pytest.raises(PathNotFoundError):
        fs.get_node("testfs", "src")
    assert fs.get_node("testfs", "dst/file.txt") is not None


def test_rename_destination_exists_raises(fs):
    fs.create_file("testfs", "a.txt")
    fs.create_file("testfs", "b.txt")
    with pytest.raises(PathAlreadyExistsError):
        fs.rename("testfs", "a.txt", "b.txt")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_file(fs):
    fs.create_file("testfs", "file.txt")
    fs.delete("testfs", "file.txt")
    with pytest.raises(PathNotFoundError):
        fs.get_node("testfs", "file.txt")


def test_delete_missing_raises(fs):
    with pytest.raises(PathNotFoundError):
        fs.delete("testfs", "ghost.txt")


def test_delete_non_empty_dir_without_recursive_raises(fs):
    fs.create_directory("testfs", "dir1")
    fs.create_file("testfs", "dir1/file.txt")
    with pytest.raises(DirectoryNotEmptyError):
        fs.delete("testfs", "dir1", recursive=False)


def test_delete_non_empty_dir_recursive(fs):
    fs.create_directory("testfs", "dir1")
    fs.create_file("testfs", "dir1/file.txt")
    fs.delete("testfs", "dir1", recursive=True)
    with pytest.raises(PathNotFoundError):
        fs.get_node("testfs", "dir1")


def test_deleted_file_not_found(fs):
    """EC-4: Deleted file operations must raise PathNotFoundError."""
    fs.create_file("testfs", "file.txt")
    fs.delete("testfs", "file.txt")
    with pytest.raises(PathNotFoundError):
        fs.read_file("testfs", "file.txt")


# ---------------------------------------------------------------------------
# SnapshotStore — blob file cleanup on filesystem delete
# ---------------------------------------------------------------------------

def test_delete_filesystem_removes_blob_files(tmp_path):
    """Regression: SnapshotStore.delete_filesystem must remove orphaned .bin files."""
    from adls_lite.store.snapshot import SnapshotStore
    store = SnapshotStore(str(tmp_path))
    store.create_filesystem("myfs")
    store.create_file("myfs", "f.txt")
    store.append("myfs", "f.txt", 0, b"hello")
    node = store.flush("myfs", "f.txt", 5)
    blob_path = tmp_path / "v1" / "blobs" / f"{node.node_id}.bin"
    assert blob_path.exists(), "blob file must exist after flush"
    store.delete_filesystem("myfs")
    assert not blob_path.exists(), "blob file must be removed after filesystem delete"
