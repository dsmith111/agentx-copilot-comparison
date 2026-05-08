"""Response header helpers per SPEC s9.1."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..store.base import FileNode, DirNode, Node, format_rfc1123

MS_VERSION = "2023-11-03"


def standard_headers() -> dict:
    """Headers required on every response."""
    now = datetime.now(timezone.utc)
    return {
        "x-ms-request-id": str(uuid.uuid4()),
        "x-ms-version": MS_VERSION,
        "Date": format_rfc1123(now),
    }


def node_headers(node: Node) -> dict:
    """Headers for path responses (file or directory)."""
    h = standard_headers()
    h["ETag"] = node.etag
    h["Last-Modified"] = format_rfc1123(node.last_modified)
    if isinstance(node, FileNode):
        h["x-ms-resource-type"] = "file"
        h["Content-Length"] = str(node.content_length)
    else:
        h["x-ms-resource-type"] = "directory"
    return h
