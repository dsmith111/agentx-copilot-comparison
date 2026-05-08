"""Error envelope helpers and canonical error codes."""
from __future__ import annotations

from fastapi.responses import JSONResponse

# Canonical code strings per SPEC s9.3
ERR_INVALID_RANGE = "InvalidRange"
ERR_INVALID_FLUSH = "InvalidFlushPosition"
ERR_INVALID_INPUT = "InvalidInput"
ERR_FS_NOT_FOUND = "FilesystemNotFound"
ERR_PATH_NOT_FOUND = "PathNotFound"
ERR_FS_EXISTS = "FilesystemAlreadyExists"
ERR_PATH_EXISTS = "PathAlreadyExists"
ERR_PATH_CONFLICT = "PathConflict"
ERR_DIR_NOT_EMPTY = "DirectoryNotEmpty"
ERR_NOT_IMPL = "NotImplemented"


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )
