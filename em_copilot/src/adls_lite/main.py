"""Run the emulator with uvicorn.

Environment variables:
* ``ADLS_LITE_DATA_DIR`` -- if set, persist state to this directory.
* ``ADLS_LITE_HOST`` -- bind address (default ``0.0.0.0``).
* ``ADLS_LITE_PORT`` -- listen port (default ``10004``).
* ``ADLS_LITE_ACCOUNT`` -- account name (default ``devstoreaccount1``).
"""
from __future__ import annotations

import os

import uvicorn

from .app import ACCOUNT, create_app
from .store import Store


def build_app():
    data_dir = os.environ.get("ADLS_LITE_DATA_DIR") or None
    account = os.environ.get("ADLS_LITE_ACCOUNT", ACCOUNT)
    return create_app(store=Store(root=data_dir), account=account)


def main() -> None:
    host = os.environ.get("ADLS_LITE_HOST", "0.0.0.0")
    port = int(os.environ.get("ADLS_LITE_PORT", "10004"))
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
