"""End-to-end smoke test using the real Azure SDK against the local emulator.

Run while the emulator is reachable at http://127.0.0.1:10004 (e.g. via
`docker compose up`). Exits 0 on success; non-zero on failure.
"""
from __future__ import annotations

import os
import sys
import uuid

from azure.storage.filedatalake import DataLakeServiceClient


# Well-known emulator account name and Azurite-compatible key. The emulator
# does not validate the signature; any non-empty key is accepted.
ACCOUNT = os.environ.get("EM_AGENTX_ACCOUNT", "devstoreaccount1")
ACCOUNT_KEY = os.environ.get(
    "EM_AGENTX_KEY",
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==",
)
ENDPOINT = os.environ.get("EM_AGENTX_ENDPOINT", "http://127.0.0.1:10004")


def main() -> int:
    account_url = f"{ENDPOINT}/{ACCOUNT}"
    print(f"[smoke] account_url={account_url}")
    svc = DataLakeServiceClient(account_url=account_url, credential=ACCOUNT_KEY)

    fs_name = f"smoke-{uuid.uuid4().hex[:8]}"
    fs = svc.create_file_system(fs_name)
    print(f"[smoke] created filesystem {fs_name}")

    try:
        fs.create_directory("dir1")
        print("[smoke] created directory dir1")

        file_client = fs.get_file_client("dir1/hello.txt")
        file_client.create_file()
        payload = b"hello from azure-storage-file-datalake smoke test"
        file_client.append_data(payload, offset=0, length=len(payload))
        file_client.flush_data(len(payload))
        print(f"[smoke] uploaded {len(payload)} bytes via create/append/flush")

        downloaded = file_client.download_file().readall()
        assert downloaded == payload, f"download mismatch: {downloaded!r}"
        print(f"[smoke] downloaded matches: {downloaded[:32]!r}...")

        names = sorted(p.name for p in fs.get_paths(recursive=True))
        assert names == ["dir1", "dir1/hello.txt"], names
        print(f"[smoke] list paths OK: {names}")

        file_client.rename_file(new_name=f"{fs_name}/dir1/renamed.txt")
        print("[smoke] renamed file")

        names_after = sorted(p.name for p in fs.get_paths(recursive=True))
        assert names_after == ["dir1", "dir1/renamed.txt"], names_after
        print(f"[smoke] list paths after rename OK: {names_after}")

        renamed = fs.get_file_client("dir1/renamed.txt")
        renamed.delete_file()
        print("[smoke] deleted file")
    finally:
        fs.delete_file_system()
        print(f"[smoke] deleted filesystem {fs_name}")

    print("[smoke] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
