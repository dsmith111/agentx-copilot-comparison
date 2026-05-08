"""Runtime configuration from environment variables."""
from __future__ import annotations

import os


class Settings:
    port: int = int(os.environ.get("ADLS_LITE_PORT", "10004"))
    host: str = os.environ.get("ADLS_LITE_HOST", "0.0.0.0")
    account: str = os.environ.get("ADLS_LITE_ACCOUNT", "devstoreaccount1")
    mode: str = os.environ.get("ADLS_LITE_MODE", "snapshot")
    data_dir: str = os.environ.get("ADLS_LITE_DATA_DIR", "/var/lib/adls-lite")
    log_level: str = os.environ.get("ADLS_LITE_LOG_LEVEL", "info")
