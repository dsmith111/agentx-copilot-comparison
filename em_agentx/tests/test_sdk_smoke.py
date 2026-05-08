"""Run the real Azure SDK against an in-process emulator instance.

This avoids needing Docker for the in-process portion of testing while still
exercising the actual `azure-storage-file-datalake` client that the example
script uses.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from azure.storage.filedatalake import DataLakeServiceClient

from em_agentx.app import create_app


WELL_KNOWN_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
    "K1SZFPTOtr/KBHBeksoGMGw=="
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    port = _free_port()
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    if not srv.started:
        srv.should_exit = True
        thread.join(timeout=2)
        pytest.fail("emulator did not start")
    try:
        yield port
    finally:
        srv.should_exit = True
        thread.join(timeout=3)


def test_sdk_full_lifecycle(server):
    port = server
    svc = DataLakeServiceClient(
        account_url=f"http://127.0.0.1:{port}/devstoreaccount1",
        credential=WELL_KNOWN_KEY,
    )
    fs_name = f"sdk-{port}"
    fs = svc.create_file_system(fs_name)
    try:
        fs.create_directory("dir1")
        fc = fs.get_file_client("dir1/hello.txt")
        fc.create_file()
        payload = b"hello from in-process SDK test"
        fc.append_data(payload, offset=0, length=len(payload))
        fc.flush_data(len(payload))

        downloaded = fc.download_file().readall()
        assert downloaded == payload

        names = sorted(p.name for p in fs.get_paths(recursive=True))
        assert names == ["dir1", "dir1/hello.txt"]

        fc.rename_file(new_name=f"{fs_name}/dir1/renamed.txt")
        names_after = sorted(p.name for p in fs.get_paths(recursive=True))
        assert names_after == ["dir1", "dir1/renamed.txt"]

        fs.get_file_client("dir1/renamed.txt").delete_file()
    finally:
        fs.delete_file_system()
