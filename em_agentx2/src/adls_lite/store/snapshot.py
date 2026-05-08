"""Snapshot (JSON + blobs) FilesystemStore for Docker persistence."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Dict, List

from .base import (
    Filesystem, FileNode, DirNode, Node,
    FilesystemAlreadyExistsError, FilesystemNotFoundError,
    PathAlreadyExistsError, PathNotFoundError, PathConflictError,
    DirectoryNotEmptyError, InvalidRangeError, InvalidInputError,
    resolve_path, resolve_parent, collect_paths,
    new_node_id, new_etag, validate_path, _now, format_rfc1123,
)
from .memory import InMemoryStore
from datetime import datetime, timezone


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return _now()


class SnapshotStore(InMemoryStore):
    """
    Extends InMemoryStore with atomic on-disk snapshot persistence.

    Layout:  {data_dir}/v1/metadata.json
             {data_dir}/v1/blobs/{node_id}.bin

    PR-1: metadata writes via temp-file + os.replace.
    PR-2: blob writes via temp-file + os.replace.
    PR-4: uncommitted buffers are NOT persisted.
    """

    def __init__(self, data_dir: str) -> None:
        super().__init__()
        self._data_dir = data_dir
        self._v1 = os.path.join(data_dir, "v1")
        self._blobs_dir = os.path.join(self._v1, "blobs")
        self._meta_path = os.path.join(self._v1, "metadata.json")
        os.makedirs(self._blobs_dir, exist_ok=True)
        self._load()

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def _node_to_dict(self, node: Node) -> dict:
        if isinstance(node, FileNode):
            return {
                "type": "file",
                "node_id": node.node_id,
                "etag": node.etag,
                "last_modified": node.last_modified.isoformat(),
            }
        d: dict = {
            "type": "directory",
            "node_id": node.node_id,
            "etag": node.etag,
            "last_modified": node.last_modified.isoformat(),
            "children": {n: self._node_to_dict(c) for n, c in node.children.items()},
        }
        return d

    def _node_from_dict(self, d: dict) -> Node:
        if d["type"] == "file":
            blob_path = os.path.join(self._blobs_dir, f"{d['node_id']}.bin")
            committed = b""
            if os.path.exists(blob_path):
                with open(blob_path, "rb") as f:
                    committed = f.read()
            return FileNode(
                node_id=d["node_id"],
                committed=committed,
                uncommitted=b"",
                etag=d["etag"],
                last_modified=_parse_dt(d["last_modified"]),
            )
        children = {n: self._node_from_dict(c) for n, c in d.get("children", {}).items()}
        return DirNode(
            node_id=d["node_id"],
            children=children,
            etag=d["etag"],
            last_modified=_parse_dt(d["last_modified"]),
        )

    def _save(self) -> None:
        data: dict = {"filesystems": {}}
        for name, fs in self._filesystems.items():
            data["filesystems"][name] = {
                "name": fs.name,
                "created": fs.created.isoformat(),
                "etag": fs.etag,
                "root": self._node_to_dict(fs.root),
            }
        tmp = self._meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self._meta_path)

    def _load(self) -> None:
        if not os.path.exists(self._meta_path):
            return
        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        for name, fsd in data.get("filesystems", {}).items():
            root = self._node_from_dict(fsd["root"])
            fs = Filesystem(
                name=fsd["name"],
                root=root,
                created=_parse_dt(fsd["created"]),
                etag=fsd["etag"],
            )
            self._filesystems[name] = fs
            self._locks[name] = asyncio.Lock()

    def _blob_path(self, node_id: str) -> str:
        return os.path.join(self._blobs_dir, f"{node_id}.bin")

    def _write_blob(self, node: FileNode) -> None:
        blob = self._blob_path(node.node_id)
        tmp = blob + ".tmp"
        with open(tmp, "wb") as f:
            f.write(node.committed)
        os.replace(tmp, blob)

    # ------------------------------------------------------------------
    # Override mutating operations to persist after each change
    # ------------------------------------------------------------------

    def create_filesystem(self, name: str) -> Filesystem:
        fs = super().create_filesystem(name)
        self._save()
        return fs

    def delete_filesystem(self, name: str) -> None:
        # Collect blob IDs before the in-memory tree is removed
        fs = self._filesystems.get(name)
        blobs_to_remove: list[str] = []
        if fs:
            self._collect_blob_ids(fs.root, blobs_to_remove)
        super().delete_filesystem(name)
        self._save()
        for bid in blobs_to_remove:
            bp = self._blob_path(bid)
            if os.path.exists(bp):
                os.remove(bp)

    def create_directory(self, fs_name: str, path: str) -> DirNode:
        node = super().create_directory(fs_name, path)
        self._save()
        return node

    def create_file(self, fs_name: str, path: str, if_none_match_star: bool = False) -> FileNode:
        node = super().create_file(fs_name, path, if_none_match_star)
        self._save()
        return node

    def flush(self, fs_name: str, path: str, position: int) -> FileNode:
        node = super().flush(fs_name, path, position)
        self._write_blob(node)
        self._save()
        return node

    def rename(self, fs_name: str, old_path: str, new_path: str) -> None:
        super().rename(fs_name, old_path, new_path)
        self._save()

    def delete(self, fs_name: str, path: str, recursive: bool = False) -> None:
        # Collect blob ids before deleting
        fs = self._filesystems.get(fs_name)
        blobs_to_remove: list[str] = []
        if fs:
            node = resolve_path(fs.root, path)
            if node is not None:
                self._collect_blob_ids(node, blobs_to_remove)
        super().delete(fs_name, path, recursive)
        self._save()
        for bid in blobs_to_remove:
            bp = self._blob_path(bid)
            if os.path.exists(bp):
                os.remove(bp)

    def _collect_blob_ids(self, node: Node, ids: list) -> None:
        if isinstance(node, FileNode):
            ids.append(node.node_id)
        elif isinstance(node, DirNode):
            for child in node.children.values():
                self._collect_blob_ids(child, ids)
