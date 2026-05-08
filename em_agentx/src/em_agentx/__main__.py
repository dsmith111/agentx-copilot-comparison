"""CLI entrypoint: `python -m em_agentx`."""
from __future__ import annotations

import os

import uvicorn

from .app import create_app


def main() -> None:
    host = os.environ.get("EM_AGENTX_HOST", "0.0.0.0")
    port = int(os.environ.get("EM_AGENTX_PORT", "10004"))
    log_level = os.environ.get("EM_AGENTX_LOG_LEVEL", "info")
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level=log_level)


if __name__ == "__main__":
    main()
