"""Entry point: python -m adls_lite"""
import uvicorn
from .config import Settings
from .app import create_app

if __name__ == "__main__":
    s = Settings()
    uvicorn.run(
        create_app(),
        host=s.host,
        port=s.port,
        log_level=s.log_level,
    )
