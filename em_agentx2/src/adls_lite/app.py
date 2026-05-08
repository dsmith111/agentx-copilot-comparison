"""FastAPI app factory.  All ADLS Gen2 DFS routes are handled via a single
catch-all dispatcher that strips the optional account prefix and branches
on (HTTP method, path scope, query parameters, and request headers).
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .config import Settings
from .protocol.errors import (
    error_response,
    ERR_INVALID_RANGE, ERR_INVALID_FLUSH, ERR_INVALID_INPUT,
    ERR_FS_NOT_FOUND, ERR_PATH_NOT_FOUND,
    ERR_FS_EXISTS, ERR_PATH_EXISTS, ERR_PATH_CONFLICT,
    ERR_DIR_NOT_EMPTY, ERR_NOT_IMPL,
)
from .protocol.headers import standard_headers, node_headers
from .store.base import (
    FilesystemAlreadyExistsError, FilesystemNotFoundError,
    PathAlreadyExistsError, PathNotFoundError, PathConflictError,
    DirectoryNotEmptyError, InvalidRangeError, InvalidInputError,
    FileNode, DirNode, format_rfc1123,
)
from .store.memory import InMemoryStore


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(store=None) -> FastAPI:
    settings = Settings()

    if store is None:
        if settings.mode == "memory":
            store = InMemoryStore()
        else:
            from .store.snapshot import SnapshotStore
            store = SnapshotStore(settings.data_dir)

    app = FastAPI(title="ADLS Gen2 Lite Emulator", docs_url=None, redoc_url=None)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health():
        return PlainTextResponse("OK", headers=standard_headers())

    # ------------------------------------------------------------------
    # Catch-all dispatcher
    # ------------------------------------------------------------------

    @app.api_route("/{rest_path:path}",
                   methods=["GET", "PUT", "DELETE", "PATCH", "HEAD"])
    async def dispatch(request: Request, rest_path: str = "") -> Response:
        # Normalize: strip optional account prefix
        account = settings.account
        if rest_path == account or rest_path.startswith(account + "/"):
            rest_path = rest_path[len(account):].lstrip("/")

        method = request.method.upper()
        query = dict(request.query_params)

        # Determine scope: account / filesystem / path
        if not rest_path:
            return await _handle_account(request, method, query, store)

        parts = rest_path.split("/", 1)
        fs_name = parts[0]
        path = parts[1] if len(parts) > 1 else ""

        if not path:
            return await _handle_filesystem(
                request, method, query, store, fs_name
            )

        return await _handle_path(
            request, method, query, store, fs_name, path
        )

    return app


# ---------------------------------------------------------------------------
# Account-scope handlers
# ---------------------------------------------------------------------------

async def _handle_account(request, method, query, store) -> Response:
    """GET /?resource=account  ->  list filesystems."""
    if method != "GET":
        return error_response(405, "MethodNotAllowed", "Method not allowed")

    fsl = store.list_filesystems()
    body = {
        "filesystems": [
            {
                "name": fs.name,
                "properties": {
                    "Last-Modified": _rfc1123(fs.created),
                    "ETag": fs.etag,
                },
            }
            for fs in fsl
        ]
    }
    return JSONResponse(content=body, headers=standard_headers())


# ---------------------------------------------------------------------------
# Filesystem-scope handlers
# ---------------------------------------------------------------------------

async def _handle_filesystem(request, method, query, store, fs_name) -> Response:
    resource = query.get("resource", "")
    comp = query.get("comp", "")

    try:
        # DFS style: ?resource=filesystem
        # Blob/container style (SDK 12.23+): ?restype=container
        restype = query.get("restype", "")
        if method == "PUT" and (resource == "filesystem" or restype == "container"):
            return _fs_create(store, fs_name)

        if method == "DELETE" and (not resource or restype == "container"):
            return _fs_delete(store, fs_name)

        if method == "GET" and resource == "filesystem":
            return await _fs_list_paths(request, store, fs_name, query)

        if method == "HEAD":
            # HEAD on filesystem - return basic info
            store.get_filesystem(fs_name)
            return Response(status_code=200, headers=standard_headers())

        return error_response(501, ERR_NOT_IMPL,
                              "This endpoint is not implemented in the lite emulator")

    except FilesystemNotFoundError:
        return error_response(404, ERR_FS_NOT_FOUND, f"Filesystem {fs_name!r} not found")
    except FilesystemAlreadyExistsError:
        return error_response(409, ERR_FS_EXISTS, f"Filesystem {fs_name!r} already exists")
    except InvalidInputError as e:
        return error_response(400, ERR_INVALID_INPUT, str(e))


def _fs_create(store, fs_name: str) -> Response:
    fs = store.create_filesystem(fs_name)
    h = standard_headers()
    h["ETag"] = fs.etag
    return Response(status_code=201, headers=h)


def _fs_delete(store, fs_name: str) -> Response:
    store.delete_filesystem(fs_name)
    return Response(status_code=202, headers=standard_headers())


async def _fs_list_paths(request, store, fs_name: str, query: dict) -> Response:
    recursive_str = query.get("recursive", "false").lower()
    recursive = recursive_str == "true"
    directory = query.get("directory", "")
    try:
        paths = store.list_paths(fs_name, recursive=recursive, directory=directory)
    except PathNotFoundError:
        paths = []
    except PathConflictError as e:
        return error_response(409, ERR_PATH_CONFLICT, str(e))
    body = {"paths": paths}
    return JSONResponse(content=body, headers=standard_headers())


# ---------------------------------------------------------------------------
# Path-scope handlers
# ---------------------------------------------------------------------------

async def _handle_path(request, method, query, store, fs_name, path) -> Response:
    resource = query.get("resource", "")
    action = query.get("action", "")
    mode = query.get("mode", "")

    async with store.lock_for(fs_name):
        try:
            if method == "PUT":
                if resource == "directory":
                    return _path_create_dir(store, fs_name, path)
                if resource == "file":
                    return _path_create_file(request, store, fs_name, path)
                # Rename: DFS API uses mode=legacy|posix|rename, old form uses renameSource query or x-ms-rename-source header
                rename_source = (
                    request.headers.get("x-ms-rename-source")
                    or query.get("renameSource")
                )
                if mode in ("rename", "legacy", "posix") or rename_source:
                    return _path_rename(request, store, fs_name, path, query)
                return error_response(501, ERR_NOT_IMPL,
                                      "PUT without recognized resource/mode parameter")

            if method == "PATCH":
                if action == "append":
                    return await _path_append(request, store, fs_name, path, query)
                if action == "flush":
                    return _path_flush(store, fs_name, path, query)
                return error_response(501, ERR_NOT_IMPL,
                                      "PATCH without recognized action parameter")

            if method == "GET":
                return _path_read(request, store, fs_name, path)

            if method == "HEAD":
                return _path_head(store, fs_name, path)

            if method == "DELETE":
                return _path_delete(store, fs_name, path, query)

            return error_response(501, ERR_NOT_IMPL, "Method not supported")

        except FilesystemNotFoundError:
            return error_response(404, ERR_FS_NOT_FOUND, f"Filesystem {fs_name!r} not found")
        except PathNotFoundError as e:
            return error_response(404, ERR_PATH_NOT_FOUND, str(e))
        except PathAlreadyExistsError as e:
            return error_response(409, ERR_PATH_EXISTS, str(e))
        except PathConflictError as e:
            return error_response(409, ERR_PATH_CONFLICT, str(e))
        except DirectoryNotEmptyError as e:
            return error_response(409, ERR_DIR_NOT_EMPTY, str(e))
        except InvalidRangeError as e:
            return error_response(400, ERR_INVALID_RANGE, str(e))
        except InvalidInputError as e:
            return error_response(400, ERR_INVALID_INPUT, str(e))


def _path_create_dir(store, fs_name: str, path: str) -> Response:
    node = store.create_directory(fs_name, path)
    return Response(status_code=201, headers=node_headers(node))


def _path_create_file(request: Request, store, fs_name: str, path: str) -> Response:
    # PUT ?resource=file always has "create-new" semantics (if_none_match_star=True).
    #
    # EC-3 acceptance requirement: DataLakeFileClient.create_file() called twice must
    # raise ResourceExistsError.  SDK 12.x does NOT send If-None-Match: * from
    # create_file() by default, so the emulator enforces create-new unconditionally.
    #
    # KNOWN LIMITATION: DataLakeFileClient.upload_data(overwrite=True) sends an
    # identical PUT ?resource=file request with no If-None-Match header -- the same
    # wire shape as a second create_file().  Because the two operations are
    # indistinguishable at the HTTP protocol level, upload_data(overwrite=True) will
    # receive a 409 PathAlreadyExists on existing files.  upload_data is not part of
    # the required SDK lifecycle for this emulator.  To overwrite an existing file use:
    #   delete_file() -> create_file() -> append_data() -> flush_data()
    node = store.create_file(fs_name, path, if_none_match_star=True)
    return Response(status_code=201, headers=node_headers(node))


def _path_rename(request: Request, store, fs_name: str, new_path: str, query: dict) -> Response:
    # Accept header (preferred) or legacy query param
    source_raw = (
        request.headers.get("x-ms-rename-source")
        or query.get("renameSource", "")
    )
    if not source_raw:
        return error_response(400, ERR_INVALID_INPUT, "Missing rename source")
    old_path = _parse_rename_source(source_raw, fs_name, store)
    if old_path is None:
        return error_response(400, ERR_INVALID_INPUT,
                              f"Cannot resolve rename source: {source_raw!r}")
    store.rename(fs_name, old_path, new_path)
    node = store.get_node(fs_name, new_path)
    h = standard_headers()
    h["ETag"] = node.etag
    h["Last-Modified"] = format_rfc1123(node.last_modified)
    return Response(status_code=201, headers=h)


def _parse_rename_source(raw: str, fs_name: str, store) -> Optional[str]:
    """
    Accept both forms:
      /devstoreaccount1/{fs}/{path}
      /{fs}/{path}
      {fs}/{path}
    Returns the path component within the filesystem.
    """
    account = Settings.account  # class-level default is fine
    raw = raw.lstrip("/")
    # Strip account prefix if present
    if raw.startswith(account + "/"):
        raw = raw[len(account) + 1:]
    # Now raw is "{fs}/{path}" or just "{path}" if same filesystem
    parts = raw.split("/", 1)
    if len(parts) == 2 and parts[0] == fs_name:
        return parts[1]
    if len(parts) == 1:
        return parts[0]
    # Interpret entire raw as a path within the filesystem (fallback)
    return raw


async def _path_append(request: Request, store, fs_name: str, path: str, query: dict) -> Response:
    try:
        position = int(query["position"])
    except (KeyError, ValueError):
        return error_response(400, ERR_INVALID_INPUT, "Missing or invalid 'position' query parameter")
    data = await request.body()
    store.append(fs_name, path, position, data)
    return Response(status_code=202, headers=standard_headers())


def _path_flush(store, fs_name: str, path: str, query: dict) -> Response:
    try:
        position = int(query["position"])
    except (KeyError, ValueError):
        return error_response(400, ERR_INVALID_FLUSH, "Missing or invalid 'position' query parameter")
    node = store.flush(fs_name, path, position)
    h = standard_headers()
    h["ETag"] = node.etag
    h["Last-Modified"] = format_rfc1123(node.last_modified)
    return Response(status_code=200, headers=h)


def _path_read(request: Request, store, fs_name: str, path: str) -> Response:
    data = store.read_file(fs_name, path)
    node = store.get_node(fs_name, path)
    # SDK sends x-ms-range; browsers/curl send Range; accept both
    range_header = request.headers.get("x-ms-range", "") or request.headers.get("Range", "")
    h = node_headers(node)

    if range_header:
        size = len(data)
        if size == 0:
            # Range not satisfiable on an empty file; mirrors Azure behaviour.
            # SDK handles 416 by falling back to a plain GET.
            h["Content-Range"] = "bytes */0"
            return Response(status_code=416, headers=h,
                            media_type="application/octet-stream")
        m = re.match(r"bytes=(\d+)-(\d+)", range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2))
            if start >= size:
                # Start offset is past EOF — RFC 7233 §4.4 requires 416.
                h["Content-Range"] = f"bytes */{size}"
                return Response(status_code=416, headers=h,
                                media_type="application/octet-stream")
            end = min(end, size - 1)
            chunk = data[start: end + 1]
            h["Content-Range"] = f"bytes {start}-{end}/{size}"
            h["Content-Length"] = str(len(chunk))
            return Response(status_code=206, content=chunk,
                            media_type="application/octet-stream", headers=h)

    h["Content-Length"] = str(len(data))
    return Response(content=data, media_type="application/octet-stream", headers=h)


def _path_head(store, fs_name: str, path: str) -> Response:
    node = store.get_node(fs_name, path)
    return Response(status_code=200, headers=node_headers(node))


def _path_delete(store, fs_name: str, path: str, query: dict) -> Response:
    recursive_str = query.get("recursive", "false").lower()
    recursive = recursive_str == "true"
    store.delete(fs_name, path, recursive=recursive)
    return Response(status_code=200, headers=standard_headers())


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _rfc1123(dt) -> str:
    from .store.base import format_rfc1123
    return format_rfc1123(dt)
