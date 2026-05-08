"""In-memory FilesystemStore implementation."""
from __future__ import annotations

import asyncio
from typing import Dict, List

from .base import (
    Filesystem, FileNode, DirNode, Node,
    FilesystemAlreadyExistsError, FilesystemNotFoundError,
    PathAlreadyExistsError, PathNotFoundError, PathConflictError,
    DirectoryNotEmptyError, InvalidRangeError, InvalidInputError,
    resolve_path, resolve_parent, collect_paths,
    new_node_id, new_etag, validate_path, _now,
)


class InMemoryStore:
    def __init__(self) -> None:
        self._filesystems: Dict[str, Filesystem] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Lock management
    # ------------------------------------------------------------------

    def lock_for(self, name: str) -> asyncio.Lock:
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    # ------------------------------------------------------------------
    # Filesystem operations
    # ------------------------------------------------------------------

    def create_filesystem(self, name: str) -> Filesystem:
        if name in self._filesystems:
            raise FilesystemAlreadyExistsError(name)
        root = DirNode(node_id=new_node_id())
        fs = Filesystem(name=name, root=root, created=_now(), etag=new_etag())
        self._filesystems[name] = fs
        self._locks[name] = asyncio.Lock()
        return fs

    def get_filesystem(self, name: str) -> Filesystem:
        fs = self._filesystems.get(name)
        if fs is None:
            raise FilesystemNotFoundError(name)
        return fs

    def delete_filesystem(self, name: str) -> None:
        if name not in self._filesystems:
            raise FilesystemNotFoundError(name)
        del self._filesystems[name]
        self._locks.pop(name, None)

    def list_filesystems(self) -> List[Filesystem]:
        return list(self._filesystems.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fs(self, name: str) -> Filesystem:
        return self.get_filesystem(name)

    def _get_node(self, fs: Filesystem, path: str) -> Node | None:
        return resolve_path(fs.root, path) if path else fs.root

    # ------------------------------------------------------------------
    # Path operations
    # ------------------------------------------------------------------

    def create_directory(self, fs_name: str, path: str) -> DirNode:
        path = validate_path(path)
        fs = self._fs(fs_name)
        parent, child_name = resolve_parent(fs.root, path)
        if parent is None:
            raise PathConflictError(f"Parent of {path!r} is not a directory")
        if child_name in parent.children:
            raise PathAlreadyExistsError(path)
        node = DirNode(node_id=new_node_id())
        parent.children[child_name] = node
        return node

    def create_file(self, fs_name: str, path: str, if_none_match_star: bool = False) -> FileNode:
        path = validate_path(path)
        fs = self._fs(fs_name)
        parent, child_name = resolve_parent(fs.root, path)
        if parent is None:
            raise PathConflictError(f"Parent of {path!r} is not a directory")
        existing = parent.children.get(child_name)
        if existing is not None:
            if if_none_match_star:
                raise PathAlreadyExistsError(path)
            if isinstance(existing, DirNode):
                raise PathConflictError(f"{path!r} is a directory")
            # Overwrite: truncate existing file
            existing.committed = b""
            existing.uncommitted = b""
            existing.etag = new_etag()
            existing.last_modified = _now()
            return existing
        node = FileNode(
            node_id=new_node_id(),
            committed=b"",
            uncommitted=b"",
            etag=new_etag(),
            last_modified=_now(),
        )
        parent.children[child_name] = node
        return node

    def append(self, fs_name: str, path: str, position: int, data: bytes) -> None:
        fs = self._fs(fs_name)
        node = resolve_path(fs.root, path)
        if node is None:
            raise PathNotFoundError(path)
        if isinstance(node, DirNode):
            raise PathConflictError(f"{path!r} is a directory")
        expected = len(node.committed) + len(node.uncommitted)
        if position != expected:
            raise InvalidRangeError(
                f"Expected position {expected}, got {position}"
            )
        node.uncommitted += data

    def flush(self, fs_name: str, path: str, position: int) -> FileNode:
        fs = self._fs(fs_name)
        node = resolve_path(fs.root, path)
        if node is None:
            raise PathNotFoundError(path)
        if isinstance(node, DirNode):
            raise PathConflictError(f"{path!r} is a directory")
        current_tail = len(node.committed) + len(node.uncommitted)
        if position != current_tail:
            raise InvalidRangeError(
                f"Expected flush position {current_tail}, got {position}"
            )
        node.committed = node.committed + node.uncommitted
        node.uncommitted = b""
        node.etag = new_etag()
        node.last_modified = _now()
        return node

    def get_node(self, fs_name: str, path: str) -> Node:
        fs = self._fs(fs_name)
        node = resolve_path(fs.root, path)
        if node is None:
            raise PathNotFoundError(path)
        return node

    def read_file(self, fs_name: str, path: str) -> bytes:
        node = self.get_node(fs_name, path)
        if isinstance(node, DirNode):
            raise PathConflictError(f"{path!r} is a directory")
        return node.committed

    def list_paths(self, fs_name: str, recursive: bool, directory: str = "") -> list:
        fs = self._fs(fs_name)
        if directory:
            start = resolve_path(fs.root, directory)
            if start is None:
                raise PathNotFoundError(directory)
            if isinstance(start, FileNode):
                raise PathConflictError(f"{directory!r} is not a directory")
            prefix = directory
        else:
            start = fs.root
            prefix = ""
        results: list = []
        collect_paths(start, prefix, recursive, results)
        return results

    def rename(self, fs_name: str, old_path: str, new_path: str) -> None:
        old_path = validate_path(old_path)
        new_path = validate_path(new_path)
        fs = self._fs(fs_name)
        old_node = resolve_path(fs.root, old_path)
        if old_node is None:
            raise PathNotFoundError(old_path)
        if resolve_path(fs.root, new_path) is not None:
            raise PathAlreadyExistsError(new_path)
        old_parent, old_name = resolve_parent(fs.root, old_path)
        if old_parent is None:
            raise InvalidInputError(f"Invalid source path {old_path!r}")
        new_parent, new_name = resolve_parent(fs.root, new_path)
        if new_parent is None:
            raise PathConflictError(
                f"Parent of destination {new_path!r} is not a directory"
            )
        del old_parent.children[old_name]
        new_parent.children[new_name] = old_node
        old_node.etag = new_etag()
        old_node.last_modified = _now()

    def delete(self, fs_name: str, path: str, recursive: bool = False) -> None:
        fs = self._fs(fs_name)
        node = resolve_path(fs.root, path)
        if node is None:
            raise PathNotFoundError(path)
        if isinstance(node, DirNode) and node.children and not recursive:
            raise DirectoryNotEmptyError(path)
        parent, child_name = resolve_parent(fs.root, path)
        if parent is None:
            raise InvalidInputError("Cannot determine parent for delete")
        del parent.children[child_name]
