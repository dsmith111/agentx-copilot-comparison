"""FastAPI application that serves the ADLS Gen2 Lite Emulator HTTP surface."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .store import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PathEntry,
    Store,
    StoreError,
    _http_date,
)


DEFAULT_ACCOUNT = os.environ.get("EM_AGENTX_ACCOUNT", "devstoreaccount1")
DEFAULT_VERSION = "2023-11-03"


def _build_store() -> Store:
    raw = os.environ.get("EM_AGENTX_DATA_DIR", "").strip()
    data_dir = Path(raw) if raw else None
    return Store(data_dir=data_dir)


def _request_id() -> str:
    return uuid.uuid4().hex


def _common_headers(extra: Optional[dict] = None) -> dict:
    headers = {
        "x-ms-request-id": _request_id(),
        "x-ms-version": DEFAULT_VERSION,
    }
    if extra:
        headers.update({k: v for k, v in extra.items() if v is not None})
    return headers


def _path_headers(entry: PathEntry) -> dict:
    # Note: Content-Length is intentionally omitted here so it is not applied
    # to PUT/PATCH responses that have an empty body. HEAD/GET responses set
    # it explicitly when needed.
    return {
        "ETag": entry.etag,
        "Last-Modified": _http_date(entry.last_modified),
        "x-ms-resource-type": "directory" if entry.is_directory else "file",
    }


def _path_size_header(entry: PathEntry) -> dict:
    return {"Content-Length": str(entry.content_size)}


def _strip_account(path: str, account: str) -> str:
    """Strip an optional leading '/{account}' from the path."""
    p = path.lstrip("/")
    prefix = account + "/"
    if p == account:
        return ""
    if p.startswith(prefix):
        return p[len(prefix) :]
    return p


def _split_fs_and_path(remaining: str) -> tuple[str, str]:
    """Split 'fs/path/to/x' into ('fs', 'path/to/x')."""
    if not remaining:
        return "", ""
    if "/" not in remaining:
        return remaining, ""
    fs, _, rest = remaining.partition("/")
    return fs, rest


def _error_response(exc: Exception) -> Response:
    status = getattr(exc, "status_code", 500)
    code = type(exc).__name__.replace("Error", "")
    body = {"error": {"code": code, "message": str(exc)}}
    return JSONResponse(status_code=status, content=body, headers=_common_headers())


def _parse_rename_source(header_value: str, account: str) -> tuple[str, str]:
    """Parse x-ms-rename-source which is '/<fs>/<path>' (optionally with account prefix)."""
    if not header_value:
        raise BadRequestError("Missing x-ms-rename-source header")
    raw = header_value.split("?", 1)[0]  # strip SAS or query
    cleaned = _strip_account(raw, account)
    fs, path = _split_fs_and_path(cleaned)
    if not fs:
        raise BadRequestError("Malformed x-ms-rename-source")
    return fs, path


def create_app(store: Optional[Store] = None, account: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="ADLS Gen2 Lite Emulator", version="0.1.0")
    app.state.store = store or _build_store()
    app.state.account = account or DEFAULT_ACCOUNT

    @app.get("/health", include_in_schema=False)
    async def health() -> PlainTextResponse:
        return PlainTextResponse("OK")

    @app.exception_handler(StoreError)
    async def _store_error_handler(request: Request, exc: StoreError):  # type: ignore[unused-ignore]
        return _error_response(exc)

    # --- Filesystem-level endpoint ----------------------------------------
    # Matches: /{account?}/{filesystem} with various query params.
    # We use a catch-all to support both with and without account prefix.

    async def _dispatch(request: Request, full_path: str) -> Response:
        store: Store = request.app.state.store
        account: str = request.app.state.account
        method = request.method.upper()
        params = request.query_params

        remaining = _strip_account(full_path, account)
        if not remaining:
            return await _root_dispatch(request, store, account, method, params)

        fs, path = _split_fs_and_path(remaining)
        if not path:
            return await _filesystem_dispatch(request, store, fs, method, params)
        return await _path_dispatch(request, store, fs, path, method, params)

    async def _root_dispatch(
        request: Request,
        store: Store,
        account: str,
        method: str,
        params,
    ) -> Response:
        # Account-level operations are out of MVP scope (list filesystems, etc.)
        if method == "GET" and params.get("resource") == "account":
            return JSONResponse(
                status_code=200,
                headers=_common_headers(),
                content={
                    "filesystems": [
                        {"name": fs.name, "lastModified": _http_date(fs.last_modified), "etag": fs.etag}
                        for fs in store.list_filesystems()
                    ]
                },
            )
        return JSONResponse(status_code=400, headers=_common_headers(), content={"error": "unsupported root request"})

    async def _filesystem_dispatch(
        request: Request,
        store: Store,
        fs: str,
        method: str,
        params,
    ) -> Response:
        # The ADLS Gen2 SDK is layered on top of the Blob SDK. Filesystem
        # create/delete/get-properties are sent in Blob "container" form
        # (?restype=container), while listing paths and namespace-aware ops
        # use DFS "?resource=filesystem".
        is_container_op = params.get("restype") == "container"
        is_dfs_filesystem_op = params.get("resource") == "filesystem"

        if method == "PUT" and (is_dfs_filesystem_op or is_container_op):
            try:
                created = store.create_filesystem(fs)
            except StoreError as e:
                return _error_response(e)
            return Response(
                status_code=201,
                headers=_common_headers({
                    "ETag": created.etag,
                    "Last-Modified": _http_date(created.last_modified),
                }),
            )

        if method == "DELETE":
            # Both DFS and Blob clients send DELETE; container-style adds restype=container.
            try:
                store.delete_filesystem(fs)
            except StoreError as e:
                return _error_response(e)
            return Response(status_code=202, headers=_common_headers())

        # List paths (DFS only)
        if method == "GET" and is_dfs_filesystem_op:
            recursive = params.get("recursive", "false").lower() == "true"
            directory = params.get("directory")
            try:
                paths = store.list_paths(fs, recursive=recursive, directory=directory)
            except StoreError as e:
                return _error_response(e)
            return JSONResponse(
                status_code=200,
                headers=_common_headers(),
                content={"paths": [p.to_listing() for p in paths]},
            )

        # Get filesystem / container properties.
        if method in ("HEAD", "GET") and (is_container_op or is_dfs_filesystem_op):
            try:
                fs_obj = store.get_filesystem(fs)
            except StoreError as e:
                return _error_response(e)
            return Response(
                status_code=200,
                headers=_common_headers({
                    "ETag": fs_obj.etag,
                    "Last-Modified": _http_date(fs_obj.last_modified),
                    "x-ms-namespace-enabled": "true",
                    "x-ms-lease-status": "unlocked",
                    "x-ms-lease-state": "available",
                    "x-ms-has-immutability-policy": "false",
                    "x-ms-has-legal-hold": "false",
                    "x-ms-default-encryption-scope": "$account-encryption-key",
                    "x-ms-deny-encryption-scope-override": "false",
                }),
            )

        return JSONResponse(
            status_code=400,
            headers=_common_headers(),
            content={"error": f"unsupported filesystem request: {method} ?{dict(params)}"},
        )

    async def _path_dispatch(
        request: Request,
        store: Store,
        fs: str,
        path: str,
        method: str,
        params,
    ) -> Response:
        account: str = request.app.state.account

        if method == "PUT":
            # Rename takes precedence (header-driven)
            rename_source = request.headers.get("x-ms-rename-source")
            if rename_source:
                try:
                    src_fs, src_path = _parse_rename_source(rename_source, account)
                    entry = store.rename(src_fs, src_path, fs, path)
                except StoreError as e:
                    return _error_response(e)
                return Response(
                    status_code=201,
                    headers=_common_headers(_path_headers(entry)),
                )

            resource = params.get("resource")
            if resource == "directory":
                try:
                    entry = store.create_directory(fs, path)
                except StoreError as e:
                    return _error_response(e)
                return Response(
                    status_code=201,
                    headers=_common_headers(_path_headers(entry)),
                )
            if resource == "file":
                try:
                    entry = store.create_file(fs, path, overwrite=True)
                except StoreError as e:
                    return _error_response(e)
                return Response(
                    status_code=201,
                    headers=_common_headers(_path_headers(entry)),
                )
            return JSONResponse(
                status_code=400,
                headers=_common_headers(),
                content={"error": f"unsupported PUT path resource={resource}"},
            )

        if method == "PATCH":
            action = params.get("action")
            try:
                position = int(params.get("position", "0"))
            except ValueError:
                return _error_response(BadRequestError("invalid position"))
            if action == "append":
                body = await request.body()
                try:
                    entry = store.append(fs, path, position, body)
                except StoreError as e:
                    return _error_response(e)
                return Response(status_code=202, headers=_common_headers())
            if action == "flush":
                try:
                    entry = store.flush(fs, path, position)
                except StoreError as e:
                    return _error_response(e)
                return Response(
                    status_code=200,
                    headers=_common_headers(_path_headers(entry)),
                )
            return JSONResponse(
                status_code=400,
                headers=_common_headers(),
                content={"error": f"unsupported PATCH action={action}"},
            )

        if method == "GET":
            range_header = (
                request.headers.get("x-ms-range")
                or request.headers.get("range")
                or request.headers.get("Range")
            )
            range_start = range_end = None
            if range_header and range_header.startswith("bytes="):
                spec = range_header[len("bytes="):]
                start_s, _, end_s = spec.partition("-")
                if start_s:
                    range_start = int(start_s)
                if end_s:
                    range_end = int(end_s)
            try:
                content, entry = store.read(fs, path, range_start, range_end)
            except StoreError as e:
                return _error_response(e)
            headers = _common_headers(_path_headers(entry))
            headers["Content-Length"] = str(len(content))
            headers["Content-Type"] = "application/octet-stream"
            status = 206 if range_header else 200
            if range_header:
                total = entry.content_size
                start = range_start or 0
                end = range_end if range_end is not None else total - 1
                if total == 0:
                    end = 0
                elif end >= total:
                    end = total - 1
                headers["Content-Range"] = f"bytes {start}-{end}/{total}"
            return Response(status_code=status, content=content, headers=headers)

        if method == "HEAD":
            try:
                entry = store.get_properties(fs, path)
            except StoreError as e:
                return _error_response(e)
            headers = _common_headers(_path_headers(entry))
            headers.update(_path_size_header(entry))
            return Response(status_code=200, headers=headers)

        if method == "DELETE":
            recursive = params.get("recursive", "false").lower() == "true"
            try:
                store.delete(fs, path, recursive=recursive)
            except StoreError as e:
                return _error_response(e)
            return Response(status_code=200, headers=_common_headers())

        return JSONResponse(
            status_code=405,
            headers=_common_headers(),
            content={"error": f"method {method} not allowed on path"},
        )

    @app.api_route("/{full_path:path}", methods=["GET", "PUT", "PATCH", "DELETE", "HEAD", "POST"])
    async def catch_all(full_path: str, request: Request) -> Response:
        return await _dispatch(request, full_path)

    return app


# Module-level ASGI app for `uvicorn em_agentx.app:app`
app = create_app()
