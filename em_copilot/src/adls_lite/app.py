"""FastAPI application implementing a subset of the Azure Data Lake Storage
Gen2 REST surface that is sufficient for the official Python SDK
(``azure-storage-file-datalake``) to drive end-to-end workflows.

The implementation deliberately keeps the surface small but functional:

* Account-name URL prefixes are tolerated (``/devstoreaccount1/...`` and
  ``/...``).
* Auth headers are accepted but never validated -- this is an emulator.
* Both DFS-flavoured (``resource=filesystem``) and Blob-flavoured
  (``restype=container``) variants of filesystem CRUD are supported because the
  DataLake SDK delegates a few operations through its blob client.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Optional
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .store import Store, http_date

ACCOUNT = "devstoreaccount1"
API_VERSION = "2023-11-03"


def _request_id() -> str:
    return str(uuid.uuid4())


def _node_headers(node, include_content_length: bool = False) -> dict:
    """Return ADLS-style metadata headers for a node.

    ``include_content_length`` should only be true for HEAD responses; for empty
    success bodies (PUT create / PATCH flush / PUT rename) the ASGI server
    derives Content-Length from the (zero-byte) body and complains if a
    file-sized header is set explicitly.
    """
    h = {
        "ETag": node.etag,
        "Last-Modified": node.last_modified,
        "x-ms-resource-type": "directory" if node.is_directory else "file",
    }
    if not node.is_directory:
        h["Content-Type"] = node.content_type
        h["x-ms-server-encrypted"] = "false"
        if include_content_length:
            h["Content-Length"] = str(len(node.data))
    if node.metadata:
        h["x-ms-properties"] = ",".join(f"{k}={v}" for k, v in node.metadata.items())
    return h


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


def _parse_rename_source(header_value: str, account: str) -> tuple[str, str]:
    """Return (filesystem, path) given an x-ms-rename-source header value.

    The header is of the form ``/{filesystem}/{path}[?sas]`` and may include the
    account name as the first segment when the SDK targets the emulator. Path
    may itself contain ``/`` segments and must be preserved in full.
    """
    if not header_value:
        return "", ""
    raw = header_value.split("?", 1)[0]
    raw = unquote(raw).lstrip("/")
    if not raw:
        return "", ""
    parts = raw.split("/", 2)
    if parts[0] == account:
        # Strip leading account name and re-split.
        rest = "/".join(parts[1:])
        parts = rest.split("/", 1)
    else:
        # Treat the leading segment as filesystem; everything else is path.
        parts = raw.split("/", 1)
    if not parts or not parts[0]:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _parse_range(header_value: str, length: int) -> tuple[int, int]:
    """Parse ``bytes=start-end`` returning inclusive byte offsets."""
    m = re.match(r"bytes=(\d*)-(\d*)", header_value or "")
    if not m:
        return 0, max(length - 1, 0)
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        return 0, max(length - 1, 0)
    if start_s == "":
        suffix = int(end_s)
        return max(length - suffix, 0), max(length - 1, 0)
    start = int(start_s)
    end = int(end_s) if end_s else length - 1
    return start, end


class _StandardHeadersMiddleware:
    """Pure-ASGI middleware that injects ADLS standard response headers.

    Implemented at the ASGI layer (rather than ``BaseHTTPMiddleware``) so it
    does not buffer response bodies. ``BaseHTTPMiddleware`` has historically
    interfered with range/keep-alive request flows, which the Azure DataLake
    SDK relies on heavily during downloads.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {h[0].lower() for h in headers}
                if b"x-ms-request-id" not in names:
                    headers.append((b"x-ms-request-id", _request_id().encode()))
                if b"x-ms-version" not in names:
                    headers.append((b"x-ms-version", API_VERSION.encode()))
                # Note: we deliberately do not append a Date header here;
                # uvicorn already adds one. Adding our own would result in
                # duplicate Date headers.
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app(store: Optional[Store] = None, account: str = ACCOUNT) -> FastAPI:
    if store is None:
        store = Store()
    app = FastAPI(title="ADLS Gen2 Lite Emulator", version="0.1.0")
    app.add_middleware(_StandardHeadersMiddleware)

    @app.get("/health", include_in_schema=False)
    def health() -> PlainTextResponse:
        return PlainTextResponse("OK")

    # ------------------------------------------------------------------ #
    # Account-level: list filesystems (Blob `comp=list` flavour)
    # ------------------------------------------------------------------ #
    def _list_filesystems_xml() -> str:
        items = "".join(
            f"<Container><Name>{xml_escape(name)}</Name>"
            f"<Properties><Last-Modified>{http_date()}</Last-Modified>"
            f"<Etag>etag</Etag></Properties></Container>"
            for name in store.list_filesystems()
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<EnumerationResults ServiceEndpoint="http://127.0.0.1/" AccountName="{account}">'
            f"<Containers>{items}</Containers><NextMarker /></EnumerationResults>"
        )

    def _list_filesystems_json() -> dict:
        return {
            "filesystems": [
                {
                    "name": name,
                    "lastModified": http_date(),
                    "etag": '"0x0"',
                }
                for name in store.list_filesystems()
            ]
        }

    # ------------------------------------------------------------------ #
    # Single catch-all dispatcher. Dispatch by method + query params.
    # ------------------------------------------------------------------ #
    def _split_path(raw: str) -> tuple[str, str]:
        """Return (filesystem, path) after stripping the optional account prefix."""
        clean = raw.strip("/")
        if not clean:
            return "", ""
        parts = clean.split("/", 2)
        if parts[0] == account:
            parts = parts[1:]
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[1]

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "PUT", "DELETE", "PATCH", "HEAD", "POST"],
        include_in_schema=False,
    )
    async def dispatch(full_path: str, request: Request) -> Response:
        method = request.method.upper()
        qp = request.query_params
        fs, path = _split_path(full_path)

        # Account-level list filesystems
        if not fs:
            if method == "GET" and (qp.get("comp") == "list" or qp.get("resource") == "account"):
                # Default to JSON shape used by DFS list filesystems; SDK's blob
                # path expects XML so honour Accept header.
                accept = request.headers.get("accept", "")
                if "xml" in accept or qp.get("comp") == "list":
                    return Response(
                        content=_list_filesystems_xml(),
                        media_type="application/xml",
                    )
                return JSONResponse(_list_filesystems_json())
            if method == "GET":
                return PlainTextResponse("ADLS Gen2 Lite Emulator")
            return _error(400, "InvalidUri", "Account-level operation not supported")

        # ---------------- Filesystem-level operations ---------------- #
        if not path:
            return await _handle_filesystem(method, fs, qp, request)

        # ---------------- Path-level operations ---------------- #
        return await _handle_path(method, fs, path, qp, request)

    async def _handle_filesystem(method: str, fs: str, qp, request: Request) -> Response:
        # PUT create filesystem
        if method == "PUT":
            if store.create_filesystem(fs):
                return Response(
                    status_code=201,
                    headers={
                        "ETag": '"0x0"',
                        "Last-Modified": http_date(),
                    },
                )
            return _error(409, "FilesystemAlreadyExists", f"filesystem '{fs}' exists")

        # DELETE filesystem
        if method == "DELETE":
            if store.delete_filesystem(fs):
                return Response(status_code=202)
            return _error(404, "FilesystemNotFound", f"filesystem '{fs}' not found")

        # HEAD filesystem (existence/properties)
        if method == "HEAD":
            if store.has_filesystem(fs):
                return Response(
                    status_code=200,
                    headers={"ETag": '"0x0"', "Last-Modified": http_date()},
                )
            return Response(status_code=404)

        # GET list paths or list blobs (blob-style `comp=list`)
        if method == "GET":
            if qp.get("comp") == "list":
                return _list_blobs_xml(fs, qp)
            recursive = qp.get("recursive", "false").lower() == "true"
            directory = qp.get("directory", "")
            paths = store.list_paths(fs, directory=directory, recursive=recursive)
            if paths is None:
                return _error(404, "FilesystemNotFound", f"filesystem '{fs}' not found")
            entries = []
            for name, node in paths:
                entry = {
                    "name": name,
                    "contentLength": str(len(node.data) if not node.is_directory else 0),
                    "etag": node.etag,
                    "lastModified": node.last_modified,
                }
                if node.is_directory:
                    entry["isDirectory"] = "true"
                entries.append(entry)
            return JSONResponse({"paths": entries})

        return _error(405, "MethodNotAllowed", method)

    def _list_blobs_xml(fs: str, qp) -> Response:
        if not store.has_filesystem(fs):
            return _error(404, "FilesystemNotFound", f"filesystem '{fs}' not found")
        prefix = qp.get("prefix", "")
        paths = store.list_paths(fs, directory="", recursive=True) or []
        items = []
        for name, node in paths:
            if node.is_directory:
                continue
            if prefix and not name.startswith(prefix):
                continue
            items.append(
                f"<Blob><Name>{xml_escape(name)}</Name>"
                f"<Properties><Last-Modified>{node.last_modified}</Last-Modified>"
                f"<Etag>{xml_escape(node.etag)}</Etag>"
                f"<Content-Length>{len(node.data)}</Content-Length>"
                f"<Content-Type>{xml_escape(node.content_type)}</Content-Type>"
                "</Properties></Blob>"
            )
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<EnumerationResults ContainerName="{xml_escape(fs)}">'
            f"<Blobs>{''.join(items)}</Blobs><NextMarker /></EnumerationResults>"
        )
        return Response(content=body, media_type="application/xml")

    async def _handle_path(
        method: str, fs: str, path: str, qp, request: Request
    ) -> Response:
        if method == "PUT":
            return await _handle_put_path(fs, path, qp, request)
        if method == "PATCH":
            return await _handle_patch_path(fs, path, qp, request)
        if method == "GET":
            return await _handle_get_path(fs, path, qp, request)
        if method == "HEAD":
            return _handle_head_path(fs, path)
        if method == "DELETE":
            return _handle_delete_path(fs, path, qp)
        return _error(405, "MethodNotAllowed", method)

    async def _handle_put_path(fs: str, path: str, qp, request: Request) -> Response:
        # Rename: mode=legacy or mode=rename with x-ms-rename-source header
        rename_source = request.headers.get("x-ms-rename-source")
        if rename_source:
            src_fs, src_path = _parse_rename_source(rename_source, account)
            if not src_fs or not src_path:
                return _error(400, "InvalidRenameSource", rename_source)
            ok, err = store.rename(src_fs, src_path, path, dst_fs=fs)
            if not ok:
                code = "PathNotFound" if "not found" in err else "RenameConflict"
                status = 404 if "not found" in err else 409
                return _error(status, code, err)
            n = store.get_node(fs, path)
            headers = _node_headers(n) if n else {}
            return Response(status_code=201, headers=headers)

        resource = qp.get("resource", "")
        if not store.has_filesystem(fs):
            return _error(404, "FilesystemNotFound", f"filesystem '{fs}' not found")

        if resource == "directory":
            ok, n, err = store.create_directory(fs, path)
            if not ok:
                return _error(409, "PathConflict", err)
            return Response(status_code=201, headers=_node_headers(n))

        if resource == "file":
            overwrite = request.headers.get("if-none-match", "").strip() != "*"
            ok, n, err = store.create_file(fs, path, overwrite=overwrite)
            if not ok:
                return _error(409, "PathConflict", err)
            return Response(status_code=201, headers=_node_headers(n))

        return _error(400, "InvalidResource", f"resource={resource!r} not supported")

    async def _handle_patch_path(fs: str, path: str, qp, request: Request) -> Response:
        action = qp.get("action", "")
        position_str = qp.get("position", "0")
        try:
            position = int(position_str)
        except ValueError:
            return _error(400, "InvalidPosition", position_str)

        if action == "append":
            body = await request.body()
            ok, err = store.append(fs, path, position, body)
            if not ok:
                if "not found" in err:
                    return _error(404, "PathNotFound", err)
                return _error(400, "InvalidAppend", err)
            return Response(status_code=202)

        if action == "flush":
            ok, err = store.flush(fs, path, position)
            if not ok:
                if "not found" in err:
                    return _error(404, "PathNotFound", err)
                return _error(400, "InvalidFlush", err)
            n = store.get_node(fs, path)
            headers = _node_headers(n) if n else {}
            return Response(status_code=200, headers=headers)

        if action == "setProperties":
            n = store.get_node(fs, path)
            if n is None:
                return _error(404, "PathNotFound", "")
            props = request.headers.get("x-ms-properties", "")
            n.metadata = _parse_properties(props)
            n.touch()
            return Response(status_code=200, headers=_node_headers(n))

        return _error(400, "InvalidAction", f"action={action!r} not supported")

    async def _handle_get_path(fs: str, path: str, qp, request: Request) -> Response:
        n = store.get_node(fs, path)
        if n is None:
            return _error(404, "PathNotFound", f"{fs}/{path}")
        if n.is_directory:
            # Directory GET is not really meaningful; return an empty body but
            # surface its properties so clients can introspect.
            return Response(status_code=200, headers=_node_headers(n))
        length = len(n.data)
        range_header = request.headers.get("range") or request.headers.get("x-ms-range")
        if range_header:
            start, end = _parse_range(range_header, length)
            end = min(end, length - 1) if length else 0
            chunk = bytes(n.data[start : end + 1]) if length else b""
            headers = dict(_node_headers(n))
            headers["Content-Range"] = f"bytes {start}-{end}/{length}"
            return Response(content=chunk, status_code=206, headers=headers)
        # Full read. The Azure blob/datalake SDK *requires* a Content-Range
        # header to compute the file size even for 200 responses, so always
        # emit one. Use ``bytes */N`` for empty files to keep the parser happy.
        headers = _node_headers(n, include_content_length=True)
        if length == 0:
            headers["Content-Range"] = "bytes */0"
        else:
            headers["Content-Range"] = f"bytes 0-{length - 1}/{length}"
        return Response(content=bytes(n.data), status_code=200, headers=headers)

    def _handle_head_path(fs: str, path: str) -> Response:
        n = store.get_node(fs, path)
        if n is None:
            return Response(status_code=404)
        # Important: ASGI/uvicorn computes the response framing from the body
        # we hand it. If we declare Content-Length: N but ship a 0-byte body,
        # the keep-alive connection state gets corrupted for subsequent
        # requests. Send the real bytes -- uvicorn strips the body from HEAD
        # responses while preserving the Content-Length header.
        headers = _node_headers(n, include_content_length=True)
        body = bytes(n.data) if not n.is_directory else b""
        return Response(content=body, status_code=200, headers=headers)

    def _handle_delete_path(fs: str, path: str, qp) -> Response:
        recursive = qp.get("recursive", "false").lower() == "true"
        ok, err = store.delete(fs, path, recursive=recursive)
        if not ok:
            if "not found" in err:
                return _error(404, "PathNotFound", err)
            return _error(409, "PathConflict", err)
        return Response(status_code=200)

    return app


def _parse_properties(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not value:
        return out
    for token in value.split(","):
        token = token.strip()
        if not token or "=" not in token:
            continue
        k, v = token.split("=", 1)
        out[k.strip()] = v.strip()
    return out
