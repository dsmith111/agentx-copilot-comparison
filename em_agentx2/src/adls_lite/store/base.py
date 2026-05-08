"""Data model, custom exceptions, and shared helpers for the filesystem store."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Union
from email.utils import formatdate
import time


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_node_id() -> str:
    return str(uuid.uuid4())


def new_etag() -> str:
    return f'"{uuid.uuid4().hex}"'


def format_rfc1123(dt: datetime) -> str:
    ts = dt.timestamp()
    return formatdate(timeval=ts, localtime=False, usegmt=True)


def validate_path(path: str) -> str:
    """Reject traversal attempts; return stripped path or raise InvalidInputError."""
    stripped = path.strip("/")
    if not stripped:
        raise InvalidInputError("Path must not be empty")
    for part in stripped.split("/"):
        if part in ("", ".", ".."):
            raise InvalidInputError(f"Invalid path segment: {part!r}")
    return stripped


# ---------------------------------------------------------------------------
# Data nodes
# ---------------------------------------------------------------------------

@dataclass
class FileNode:
    node_id: str
    committed: bytes
    uncommitted: bytes
    etag: str
    last_modified: datetime

    @property
    def content_length(self) -> int:
        return len(self.committed)


@dataclass
class DirNode:
    node_id: str
    children: Dict[str, "Node"] = field(default_factory=dict)
    etag: str = field(default_factory=new_etag)
    last_modified: datetime = field(default_factory=_now)


Node = Union[FileNode, DirNode]


@dataclass
class Filesystem:
    name: str
    root: DirNode
    created: datetime
    etag: str


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class FilesystemNotFoundError(Exception):
    pass


class FilesystemAlreadyExistsError(Exception):
    pass


class PathNotFoundError(Exception):
    pass


class PathAlreadyExistsError(Exception):
    pass


class PathConflictError(Exception):
    """Parent is a file, or destination parent invalid."""
    pass


class DirectoryNotEmptyError(Exception):
    pass


class InvalidRangeError(Exception):
    pass


class InvalidInputError(Exception):
    pass


# ---------------------------------------------------------------------------
# Tree traversal helpers (shared by memory + snapshot stores)
# ---------------------------------------------------------------------------

def resolve_path(root: DirNode, path: str) -> Optional[Node]:
    """Return the node at `path`, or None if not found."""
    if not path:
        return root
    current: Node = root
    for part in path.split("/"):
        if not isinstance(current, DirNode):
            return None
        child = current.children.get(part)
        if child is None:
            return None
        current = child
    return current


def resolve_parent(root: DirNode, path: str) -> tuple[Optional[DirNode], str]:
    """Return (parent_dir, child_name) or (None, child_name) if parent not a dir."""
    parts = path.split("/")
    if len(parts) == 1:
        return root, parts[0]
    parent_path = "/".join(parts[:-1])
    child_name = parts[-1]
    parent = resolve_path(root, parent_path)
    if not isinstance(parent, DirNode):
        return None, child_name
    return parent, child_name


def collect_paths(node: DirNode, prefix: str, recursive: bool, results: list) -> None:
    """Depth-first, alphabetical collection of path dicts under node."""
    for name in sorted(node.children):
        child = node.children[name]
        child_path = f"{prefix}/{name}" if prefix else name
        if isinstance(child, FileNode):
            results.append({
                "name": child_path,
                "contentLength": str(child.content_length),
                "lastModified": format_rfc1123(child.last_modified),
                "etag": child.etag,
            })
        else:
            results.append({
                "name": child_path,
                "isDirectory": "true",
                "lastModified": format_rfc1123(child.last_modified),
                "etag": child.etag,
            })
            if recursive:
                collect_paths(child, child_path, recursive, results)
