"""In-memory + optional disk-persistent data store for the ADLS Gen2 Lite Emulator.

The store models a hierarchical namespace per filesystem. Each path is either
a directory or a file. Files have committed bytes plus uncommitted "staged"
bytes that grow via `append` and become permanent on `flush`.

Persistence (when enabled) is intentionally simple: each filesystem is
serialized to a single JSON metadata file plus a directory of blob files keyed
by a stable blob id.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


# --- Errors -----------------------------------------------------------------


class StoreError(Exception):
    status_code = 500


class NotFoundError(StoreError):
    status_code = 404


class ConflictError(StoreError):
    status_code = 409


class BadRequestError(StoreError):
    status_code = 400


# --- Helpers ----------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _http_date(dt: datetime) -> str:
    # RFC 7231 IMF-fixdate, e.g. "Sun, 06 Nov 1994 08:49:37 GMT"
    return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _new_etag() -> str:
    return '"' + uuid.uuid4().hex + '"'


def normalize_path(path: str) -> str:
    """Normalize a logical path inside a filesystem.

    Empty string represents the filesystem root. Otherwise the result has no
    leading or trailing slash and no consecutive slashes.
    """
    if path is None:
        return ""
    p = path.strip()
    if not p:
        return ""
    # collapse multiple slashes, strip leading/trailing
    parts = [seg for seg in p.split("/") if seg]
    return "/".join(parts)


def parent_of(path: str) -> str:
    p = normalize_path(path)
    if "/" not in p:
        return ""
    return p.rsplit("/", 1)[0]


# --- Data classes -----------------------------------------------------------


@dataclass
class PathEntry:
    name: str
    is_directory: bool
    blob_id: Optional[str] = None  # only for files
    content_size: int = 0
    staged: bytearray = field(default_factory=bytearray)  # uncommitted appends
    properties: dict = field(default_factory=dict)
    last_modified: datetime = field(default_factory=_now)
    etag: str = field(default_factory=_new_etag)

    def to_listing(self) -> dict:
        item: dict = {
            "name": self.name,
            "contentLength": str(self.content_size),
            "etag": self.etag,
            "lastModified": _http_date(self.last_modified),
            "owner": "$superuser",
            "group": "$superuser",
            "permissions": "rwxr-x---" if self.is_directory else "rw-r-----",
        }
        if self.is_directory:
            item["isDirectory"] = "true"
        return item


@dataclass
class Filesystem:
    name: str
    paths: dict[str, PathEntry] = field(default_factory=dict)
    last_modified: datetime = field(default_factory=_now)
    etag: str = field(default_factory=_new_etag)


# --- Store ------------------------------------------------------------------


class Store:
    """Thread-safe in-memory store with optional disk persistence."""

    def __init__(self, data_dir: Optional[Path] = None):
        self._lock = threading.RLock()
        self._filesystems: dict[str, Filesystem] = {}
        self._data_dir = Path(data_dir) if data_dir else None
        if self._data_dir is not None:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._load_all()

    # -- persistence ---------------------------------------------------------

    def _fs_meta_path(self, fs_name: str) -> Path:
        assert self._data_dir is not None
        return self._data_dir / f"{fs_name}.json"

    def _fs_blob_dir(self, fs_name: str) -> Path:
        assert self._data_dir is not None
        d = self._data_dir / f"{fs_name}_blobs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_all(self) -> None:
        assert self._data_dir is not None
        for meta in sorted(self._data_dir.glob("*.json")):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                continue
            fs = Filesystem(
                name=data["name"],
                last_modified=datetime.fromisoformat(data["last_modified"]),
                etag=data["etag"],
            )
            for p in data.get("paths", []):
                entry = PathEntry(
                    name=p["name"],
                    is_directory=p["is_directory"],
                    blob_id=p.get("blob_id"),
                    content_size=p.get("content_size", 0),
                    properties=p.get("properties", {}),
                    last_modified=datetime.fromisoformat(p["last_modified"]),
                    etag=p["etag"],
                )
                fs.paths[entry.name] = entry
            self._filesystems[fs.name] = fs

    def _persist_fs(self, fs: Filesystem) -> None:
        if self._data_dir is None:
            return
        data = {
            "name": fs.name,
            "etag": fs.etag,
            "last_modified": fs.last_modified.isoformat(),
            "paths": [
                {
                    "name": p.name,
                    "is_directory": p.is_directory,
                    "blob_id": p.blob_id,
                    "content_size": p.content_size,
                    "properties": p.properties,
                    "last_modified": p.last_modified.isoformat(),
                    "etag": p.etag,
                }
                for p in fs.paths.values()
            ],
        }
        meta = self._fs_meta_path(fs.name)
        tmp = meta.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, meta)

    def _delete_fs_files(self, fs_name: str) -> None:
        if self._data_dir is None:
            return
        meta = self._fs_meta_path(fs_name)
        if meta.exists():
            meta.unlink()
        blob_dir = self._data_dir / f"{fs_name}_blobs"
        if blob_dir.exists():
            for child in blob_dir.iterdir():
                child.unlink()
            blob_dir.rmdir()

    def _write_blob(self, fs_name: str, blob_id: str, content: bytes) -> None:
        if self._data_dir is None:
            return
        path = self._fs_blob_dir(fs_name) / blob_id
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, path)

    def _read_blob(self, fs_name: str, blob_id: str) -> bytes:
        if self._data_dir is None:
            return b""
        path = self._fs_blob_dir(fs_name) / blob_id
        return path.read_bytes() if path.exists() else b""

    def _delete_blob(self, fs_name: str, blob_id: Optional[str]) -> None:
        if self._data_dir is None or not blob_id:
            return
        path = self._fs_blob_dir(fs_name) / blob_id
        if path.exists():
            path.unlink()

    # In-memory blobs (for in-memory mode) -----------------------------------
    # When data_dir is None we keep file content directly on the entry via a
    # second attribute set; we use a side dict keyed by (fs, blob_id).
    _mem_blobs: dict[tuple[str, str], bytes]

    def _get_content(self, fs_name: str, entry: PathEntry) -> bytes:
        if entry.blob_id is None:
            return b""
        if self._data_dir is None:
            return self._mem_blobs.get((fs_name, entry.blob_id), b"")
        return self._read_blob(fs_name, entry.blob_id)

    def _set_content(self, fs_name: str, entry: PathEntry, content: bytes) -> None:
        if entry.blob_id is None:
            entry.blob_id = uuid.uuid4().hex
        entry.content_size = len(content)
        if self._data_dir is None:
            self._mem_blobs[(fs_name, entry.blob_id)] = bytes(content)
        else:
            self._write_blob(fs_name, entry.blob_id, content)

    def _drop_content(self, fs_name: str, entry: PathEntry) -> None:
        if entry.blob_id is None:
            return
        if self._data_dir is None:
            self._mem_blobs.pop((fs_name, entry.blob_id), None)
        else:
            self._delete_blob(fs_name, entry.blob_id)
        entry.blob_id = None
        entry.content_size = 0

    # Initialize the in-memory blob dict regardless of mode (cheap).
    def __post_init__(self) -> None:  # not a dataclass; placeholder
        pass

    # --- public API ---------------------------------------------------------

    def reset(self) -> None:
        """Wipe all in-memory state (and on-disk state if persistent)."""
        with self._lock:
            for name in list(self._filesystems.keys()):
                self._delete_fs_files(name)
            self._filesystems.clear()
            self._mem_blobs = {}

    # --- filesystem ops -----------------------------------------------------

    def create_filesystem(self, name: str) -> Filesystem:
        with self._lock:
            if name in self._filesystems:
                raise ConflictError(f"Filesystem '{name}' already exists")
            fs = Filesystem(name=name)
            self._filesystems[name] = fs
            self._persist_fs(fs)
            return fs

    def get_filesystem(self, name: str) -> Filesystem:
        fs = self._filesystems.get(name)
        if fs is None:
            raise NotFoundError(f"Filesystem '{name}' not found")
        return fs

    def list_filesystems(self) -> list[Filesystem]:
        return list(self._filesystems.values())

    def delete_filesystem(self, name: str) -> None:
        with self._lock:
            if name not in self._filesystems:
                raise NotFoundError(f"Filesystem '{name}' not found")
            del self._filesystems[name]
            self._delete_fs_files(name)

    # --- path ops -----------------------------------------------------------

    def _get_entry(self, fs: Filesystem, path: str) -> PathEntry:
        p = normalize_path(path)
        entry = fs.paths.get(p)
        if entry is None:
            raise NotFoundError(f"Path '{p}' not found in filesystem '{fs.name}'")
        return entry

    def _ensure_parents(self, fs: Filesystem, path: str) -> None:
        """Create parent directory entries on demand (Azure-like behavior)."""
        p = normalize_path(path)
        if "/" not in p:
            return
        parts = p.split("/")[:-1]
        cur = ""
        for seg in parts:
            cur = f"{cur}/{seg}" if cur else seg
            existing = fs.paths.get(cur)
            if existing is None:
                fs.paths[cur] = PathEntry(name=cur, is_directory=True)
            elif not existing.is_directory:
                raise ConflictError(f"Parent '{cur}' is a file, not a directory")

    def create_directory(self, fs_name: str, path: str) -> PathEntry:
        with self._lock:
            fs = self.get_filesystem(fs_name)
            p = normalize_path(path)
            if not p:
                raise BadRequestError("Cannot create empty path")
            existing = fs.paths.get(p)
            if existing is not None:
                if existing.is_directory:
                    # Idempotent: refresh modified time & etag like Azure.
                    existing.last_modified = _now()
                    existing.etag = _new_etag()
                    self._persist_fs(fs)
                    return existing
                raise ConflictError(f"Path '{p}' exists as a file")
            self._ensure_parents(fs, p)
            entry = PathEntry(name=p, is_directory=True)
            fs.paths[p] = entry
            fs.last_modified = _now()
            self._persist_fs(fs)
            return entry

    def create_file(self, fs_name: str, path: str, overwrite: bool = True) -> PathEntry:
        with self._lock:
            fs = self.get_filesystem(fs_name)
            p = normalize_path(path)
            if not p:
                raise BadRequestError("Cannot create empty path")
            existing = fs.paths.get(p)
            if existing is not None:
                if existing.is_directory:
                    raise ConflictError(f"Path '{p}' exists as a directory")
                if not overwrite:
                    raise ConflictError(f"Path '{p}' already exists")
                # overwrite: drop old content, reset staged
                self._drop_content(fs.name, existing)
                existing.staged = bytearray()
                existing.content_size = 0
                existing.last_modified = _now()
                existing.etag = _new_etag()
                self._persist_fs(fs)
                return existing
            self._ensure_parents(fs, p)
            entry = PathEntry(name=p, is_directory=False)
            fs.paths[p] = entry
            fs.last_modified = _now()
            self._persist_fs(fs)
            return entry

    def append(self, fs_name: str, path: str, position: int, data: bytes) -> PathEntry:
        with self._lock:
            fs = self.get_filesystem(fs_name)
            entry = self._get_entry(fs, path)
            if entry.is_directory:
                raise ConflictError("Cannot append to a directory")
            current_staged = len(entry.staged)
            if position != current_staged:
                raise BadRequestError(
                    f"Invalid append position {position}; expected {current_staged}"
                )
            entry.staged.extend(data)
            entry.last_modified = _now()
            return entry

    def flush(self, fs_name: str, path: str, position: int) -> PathEntry:
        with self._lock:
            fs = self.get_filesystem(fs_name)
            entry = self._get_entry(fs, path)
            if entry.is_directory:
                raise ConflictError("Cannot flush a directory")
            if position != len(entry.staged):
                raise BadRequestError(
                    f"Invalid flush position {position}; staged size is {len(entry.staged)}"
                )
            self._set_content(fs.name, entry, bytes(entry.staged))
            entry.staged = bytearray()
            entry.last_modified = _now()
            entry.etag = _new_etag()
            self._persist_fs(fs)
            return entry

    def read(
        self,
        fs_name: str,
        path: str,
        range_start: Optional[int] = None,
        range_end: Optional[int] = None,
    ) -> tuple[bytes, PathEntry]:
        with self._lock:
            fs = self.get_filesystem(fs_name)
            entry = self._get_entry(fs, path)
            if entry.is_directory:
                raise ConflictError("Cannot read a directory")
            content = self._get_content(fs.name, entry)
            if range_start is None and range_end is None:
                return content, entry
            start = range_start or 0
            total = len(content)
            end = range_end if range_end is not None else total - 1
            # Clamp to available content (SDKs frequently send a default large
            # range, e.g. bytes=0-4194303, that exceeds the file size).
            if end >= total:
                end = total - 1
            if total == 0:
                return b"", entry
            if start < 0 or start > end:
                raise BadRequestError("Invalid range")
            return content[start : end + 1], entry

    def get_properties(self, fs_name: str, path: str) -> PathEntry:
        with self._lock:
            fs = self.get_filesystem(fs_name)
            return self._get_entry(fs, path)

    def list_paths(
        self,
        fs_name: str,
        recursive: bool = False,
        directory: Optional[str] = None,
    ) -> list[PathEntry]:
        with self._lock:
            fs = self.get_filesystem(fs_name)
            base = normalize_path(directory or "")
            results: list[PathEntry] = []
            for name, entry in fs.paths.items():
                if base:
                    if not (name == base or name.startswith(base + "/")):
                        continue
                    if name == base:
                        continue  # exclude the directory itself
                    relative = name[len(base) + 1 :]
                else:
                    relative = name
                if not recursive and "/" in relative:
                    continue
                results.append(entry)
            results.sort(key=lambda e: e.name)
            return results

    def rename(
        self,
        source_fs: str,
        source_path: str,
        dest_fs: str,
        dest_path: str,
        overwrite: bool = True,
    ) -> PathEntry:
        with self._lock:
            src_fs = self.get_filesystem(source_fs)
            dst_fs = self.get_filesystem(dest_fs)
            src = normalize_path(source_path)
            dst = normalize_path(dest_path)
            if not src or not dst:
                raise BadRequestError("Empty rename path")
            src_entry = src_fs.paths.get(src)
            if src_entry is None:
                raise NotFoundError(f"Source '{src}' not found")
            existing = dst_fs.paths.get(dst)
            if existing is not None:
                if not overwrite:
                    raise ConflictError(f"Destination '{dst}' already exists")
                if existing.is_directory:
                    raise ConflictError("Cannot overwrite directory")
                self._drop_content(dst_fs.name, existing)
                del dst_fs.paths[dst]
            self._ensure_parents(dst_fs, dst)

            # Move source (and descendants if directory) atomically.
            to_move: list[tuple[str, str, PathEntry]] = []
            if src_entry.is_directory:
                prefix = src + "/"
                for name, entry in list(src_fs.paths.items()):
                    if name == src:
                        to_move.append((name, dst, entry))
                    elif name.startswith(prefix):
                        new_name = dst + "/" + name[len(prefix):]
                        to_move.append((name, new_name, entry))
            else:
                to_move.append((src, dst, src_entry))

            # If cross-filesystem, also move the blob bytes.
            for old_name, new_name, entry in to_move:
                del src_fs.paths[old_name]
                if source_fs != dest_fs and entry.blob_id and not entry.is_directory:
                    content = self._get_content(source_fs, entry)
                    # Drop from source
                    if self._data_dir is None:
                        self._mem_blobs.pop((source_fs, entry.blob_id), None)
                    else:
                        self._delete_blob(source_fs, entry.blob_id)
                    # Re-create in destination
                    new_blob_id = uuid.uuid4().hex
                    entry.blob_id = new_blob_id
                    if self._data_dir is None:
                        self._mem_blobs[(dest_fs, new_blob_id)] = content
                    else:
                        self._write_blob(dest_fs, new_blob_id, content)
                entry.name = new_name
                entry.last_modified = _now()
                entry.etag = _new_etag()
                dst_fs.paths[new_name] = entry

            src_fs.last_modified = _now()
            dst_fs.last_modified = _now()
            self._persist_fs(src_fs)
            if src_fs is not dst_fs:
                self._persist_fs(dst_fs)
            return dst_fs.paths[dst]

    def delete(self, fs_name: str, path: str, recursive: bool = False) -> None:
        with self._lock:
            fs = self.get_filesystem(fs_name)
            p = normalize_path(path)
            entry = fs.paths.get(p)
            if entry is None:
                raise NotFoundError(f"Path '{p}' not found")
            if entry.is_directory:
                prefix = p + "/"
                children = [n for n in fs.paths if n.startswith(prefix)]
                if children and not recursive:
                    raise ConflictError("Directory is not empty; use recursive=true")
                for child_name in children:
                    child = fs.paths[child_name]
                    if not child.is_directory:
                        self._drop_content(fs.name, child)
                    del fs.paths[child_name]
                del fs.paths[p]
            else:
                self._drop_content(fs.name, entry)
                del fs.paths[p]
            fs.last_modified = _now()
            self._persist_fs(fs)

    # Iteration helper for tests.
    def iter_paths(self, fs_name: str) -> Iterator[PathEntry]:
        fs = self.get_filesystem(fs_name)
        return iter(list(fs.paths.values()))


# Workaround: ensure _mem_blobs always exists.
_orig_init = Store.__init__


def _patched_init(self, *a, **kw):  # type: ignore[no-redef]
    self._mem_blobs = {}
    _orig_init(self, *a, **kw)


Store.__init__ = _patched_init  # type: ignore[assignment]
