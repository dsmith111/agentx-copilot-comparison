"""In-memory hierarchical-namespace store with optional disk persistence.

The store models filesystems containing paths. A path is either a directory or a
file. Files keep raw bytes plus a small metadata block (etag, last-modified).
Directories are real nodes (parent directories are auto-created on demand).

When ``root`` is provided the store snapshots its full state to disk after every
mutation as a single JSON file. This keeps the implementation tiny -- the
emulator targets test workloads, not large data volumes.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


def http_date() -> str:
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def new_etag() -> str:
    return f'"0x{uuid.uuid4().hex.upper()}"'


@dataclass
class Node:
    is_directory: bool
    data: bytearray = field(default_factory=bytearray)
    etag: str = field(default_factory=new_etag)
    last_modified: str = field(default_factory=http_date)
    content_type: str = "application/octet-stream"
    metadata: Dict[str, str] = field(default_factory=dict)

    def touch(self) -> None:
        self.etag = new_etag()
        self.last_modified = http_date()


def _norm(path: str) -> str:
    return path.strip("/").replace("\\", "/")


class Store:
    def __init__(self, root: Optional[str] = None):
        self.root = root
        self._lock = threading.RLock()
        self._fs: Dict[str, Dict[str, Node]] = {}
        if self.root:
            os.makedirs(self.root, exist_ok=True)
            self._load()

    # ------------------------------------------------------------------ #
    # Filesystem-level operations
    # ------------------------------------------------------------------ #
    def create_filesystem(self, name: str) -> bool:
        with self._lock:
            if name in self._fs:
                return False
            self._fs[name] = {}
            self._persist()
            return True

    def delete_filesystem(self, name: str) -> bool:
        with self._lock:
            if name not in self._fs:
                return False
            del self._fs[name]
            self._persist()
            return True

    def list_filesystems(self) -> List[str]:
        with self._lock:
            return sorted(self._fs.keys())

    def has_filesystem(self, name: str) -> bool:
        with self._lock:
            return name in self._fs

    # ------------------------------------------------------------------ #
    # Path-level operations
    # ------------------------------------------------------------------ #
    def get_node(self, fs: str, path: str) -> Optional[Node]:
        with self._lock:
            if fs not in self._fs:
                return None
            return self._fs[fs].get(_norm(path))

    def create_directory(self, fs: str, path: str) -> Tuple[bool, Optional[Node], str]:
        with self._lock:
            if fs not in self._fs:
                return False, None, "filesystem not found"
            p = _norm(path)
            if not p:
                return False, None, "path required"
            existing = self._fs[fs].get(p)
            if existing is not None:
                if existing.is_directory:
                    return True, existing, ""
                return False, existing, "path exists as file"
            self._ensure_parents(fs, p)
            n = Node(is_directory=True)
            self._fs[fs][p] = n
            self._persist()
            return True, n, ""

    def create_file(self, fs: str, path: str, overwrite: bool = True) -> Tuple[bool, Optional[Node], str]:
        with self._lock:
            if fs not in self._fs:
                return False, None, "filesystem not found"
            p = _norm(path)
            if not p:
                return False, None, "path required"
            existing = self._fs[fs].get(p)
            if existing is not None:
                if existing.is_directory:
                    return False, existing, "path exists as directory"
                if not overwrite:
                    return False, existing, "path exists"
            self._ensure_parents(fs, p)
            n = Node(is_directory=False)
            self._fs[fs][p] = n
            self._persist()
            return True, n, ""

    def append(self, fs: str, path: str, position: int, data: bytes) -> Tuple[bool, str]:
        with self._lock:
            if fs not in self._fs:
                return False, "filesystem not found"
            n = self._fs[fs].get(_norm(path))
            if n is None or n.is_directory:
                return False, "file not found"
            if position != len(n.data):
                return False, f"invalid position {position}, expected {len(n.data)}"
            n.data.extend(data)
            # Note: append does not update etag/lastmod -- flush does.
            self._persist()
            return True, ""

    def flush(self, fs: str, path: str, position: int) -> Tuple[bool, str]:
        with self._lock:
            if fs not in self._fs:
                return False, "filesystem not found"
            n = self._fs[fs].get(_norm(path))
            if n is None or n.is_directory:
                return False, "file not found"
            if position < 0 or position > len(n.data):
                return False, f"invalid flush position {position}, file length {len(n.data)}"
            del n.data[position:]
            n.touch()
            self._persist()
            return True, ""

    def read(
        self, fs: str, path: str, start: int = 0, end: Optional[int] = None
    ) -> Optional[bytes]:
        with self._lock:
            if fs not in self._fs:
                return None
            n = self._fs[fs].get(_norm(path))
            if n is None or n.is_directory:
                return None
            if end is None:
                return bytes(n.data[start:])
            return bytes(n.data[start : end + 1])

    def list_paths(
        self, fs: str, directory: str = "", recursive: bool = False
    ) -> Optional[List[Tuple[str, Node]]]:
        with self._lock:
            if fs not in self._fs:
                return None
            prefix = _norm(directory)
            results: List[Tuple[str, Node]] = []
            for p, n in self._fs[fs].items():
                if prefix:
                    if p == prefix:
                        continue
                    if not p.startswith(prefix + "/"):
                        continue
                    sub = p[len(prefix) + 1 :]
                else:
                    sub = p
                if not recursive and "/" in sub:
                    continue
                results.append((p, n))
            results.sort(key=lambda x: x[0])
            return results

    def delete(self, fs: str, path: str, recursive: bool = False) -> Tuple[bool, str]:
        with self._lock:
            if fs not in self._fs:
                return False, "filesystem not found"
            p = _norm(path)
            n = self._fs[fs].get(p)
            if n is None:
                return False, "not found"
            if n.is_directory:
                children = [k for k in self._fs[fs] if k.startswith(p + "/")]
                if children and not recursive:
                    return False, "directory not empty"
                for c in children:
                    del self._fs[fs][c]
            del self._fs[fs][p]
            self._persist()
            return True, ""

    def rename(
        self, fs: str, src_path: str, dst_path: str, dst_fs: Optional[str] = None
    ) -> Tuple[bool, str]:
        with self._lock:
            if fs not in self._fs:
                return False, "source filesystem not found"
            target_fs = dst_fs or fs
            if target_fs not in self._fs:
                return False, "destination filesystem not found"
            sp = _norm(src_path)
            dp = _norm(dst_path)
            if not sp or not dp:
                return False, "source and destination required"
            n = self._fs[fs].get(sp)
            if n is None:
                return False, "source not found"
            existing = self._fs[target_fs].get(dp)
            if existing is not None:
                if existing.is_directory:
                    return False, "destination is a directory"
                del self._fs[target_fs][dp]
            self._ensure_parents(target_fs, dp)
            to_move = [(sp, n)]
            if n.is_directory:
                to_move.extend(
                    (k, v) for k, v in self._fs[fs].items() if k.startswith(sp + "/")
                )
            for old, node in to_move:
                new = dp + old[len(sp) :]
                self._fs[target_fs][new] = node
                if old in self._fs[fs] and (target_fs != fs or old != new):
                    del self._fs[fs][old]
            self._persist()
            return True, ""

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _ensure_parents(self, fs: str, path: str) -> None:
        parts = path.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if parent and parent not in self._fs[fs]:
                self._fs[fs][parent] = Node(is_directory=True)

    def _snapshot_path(self) -> str:
        assert self.root is not None
        return os.path.join(self.root, "snapshot.json")

    def _persist(self) -> None:
        if not self.root:
            return
        snap = {"filesystems": {}}
        for fs, nodes in self._fs.items():
            snap["filesystems"][fs] = {
                p: {
                    "is_directory": n.is_directory,
                    "etag": n.etag,
                    "last_modified": n.last_modified,
                    "content_type": n.content_type,
                    "metadata": n.metadata,
                    "data_b64": (
                        base64.b64encode(bytes(n.data)).decode("ascii")
                        if not n.is_directory
                        else ""
                    ),
                }
                for p, n in nodes.items()
            }
        tmp = self._snapshot_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f)
        os.replace(tmp, self._snapshot_path())

    def _load(self) -> None:
        path = self._snapshot_path()
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            snap = json.load(f)
        for fs, nodes in snap.get("filesystems", {}).items():
            self._fs[fs] = {}
            for p, meta in nodes.items():
                n = Node(
                    is_directory=meta["is_directory"],
                    etag=meta.get("etag", new_etag()),
                    last_modified=meta.get("last_modified", http_date()),
                    content_type=meta.get("content_type", "application/octet-stream"),
                    metadata=meta.get("metadata", {}),
                )
                if not n.is_directory and meta.get("data_b64"):
                    n.data = bytearray(base64.b64decode(meta["data_b64"]))
                self._fs[fs][p] = n
