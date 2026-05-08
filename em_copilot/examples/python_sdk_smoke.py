"""End-to-end smoke test that drives the local ADLS Gen2 Lite emulator with the
real ``azure-storage-file-datalake`` SDK. No live Azure resources are used.

Pre-requisites
--------------
* The emulator is running locally on ``http://127.0.0.1:10004``.
* ``azure-storage-file-datalake`` is installed (``pip install
  azure-storage-file-datalake``).

Run with::

    python examples/python_sdk_smoke.py
"""
from __future__ import annotations

import os
import sys
import uuid

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.filedatalake import DataLakeServiceClient

# Azurite-compatible well-known account key. The emulator does not validate
# signatures, but the SDK requires *some* credential to construct requests.
ACCOUNT_NAME = os.environ.get("ADLS_LITE_ACCOUNT", "devstoreaccount1")
ACCOUNT_KEY = os.environ.get(
    "ADLS_LITE_ACCOUNT_KEY",
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==",
)
ENDPOINT = os.environ.get(
    "ADLS_LITE_ENDPOINT", f"http://127.0.0.1:10004/{ACCOUNT_NAME}"
)


def main() -> int:
    fs_name = f"smoke-{uuid.uuid4().hex[:8]}"
    print(f"[smoke] endpoint={ENDPOINT} filesystem={fs_name}")

    service = DataLakeServiceClient(account_url=ENDPOINT, credential=ACCOUNT_KEY)

    # 1. Create filesystem ------------------------------------------------- #
    fs_client = service.create_file_system(fs_name)
    print("[smoke] created filesystem")

    try:
        # 2. Create directory --------------------------------------------- #
        dir_client = fs_client.create_directory("uploads/2026")
        print("[smoke] created directory uploads/2026")

        # 3. Create file + 4. append + 5. flush --------------------------- #
        file_client = dir_client.create_file("greeting.txt")
        payload = b"hello from the adls gen2 lite emulator"
        file_client.append_data(data=payload, offset=0, length=len(payload))
        file_client.flush_data(len(payload))
        print(f"[smoke] uploaded {len(payload)} bytes via create/append/flush")

        # 6. Read it back ------------------------------------------------- #
        downloaded = file_client.download_file().readall()
        assert downloaded == payload, (downloaded, payload)
        print("[smoke] downloaded bytes match")

        # Properties (HEAD) ---------------------------------------------- #
        props = file_client.get_file_properties()
        assert props.size == len(payload), props.size
        print(f"[smoke] get_file_properties size={props.size}")

        # 7. List paths --------------------------------------------------- #
        names = sorted(p.name for p in fs_client.get_paths(recursive=True))
        expected_subset = {"uploads", "uploads/2026", "uploads/2026/greeting.txt"}
        assert expected_subset.issubset(set(names)), names
        print(f"[smoke] listed paths: {names}")

        # 8. Rename file -------------------------------------------------- #
        renamed = file_client.rename_file(f"{fs_name}/uploads/2026/hello.txt")
        assert renamed.path_name == "uploads/2026/hello.txt", renamed.path_name
        # The original path should no longer exist.
        try:
            file_client.get_file_properties()
        except ResourceNotFoundError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("old path still resolvable after rename")
        print("[smoke] renamed file to uploads/2026/hello.txt")

        # Read after rename
        new_client = fs_client.get_file_client("uploads/2026/hello.txt")
        assert new_client.download_file().readall() == payload
        print("[smoke] re-read renamed file OK")

        # 9. Delete file -------------------------------------------------- #
        new_client.delete_file()
        try:
            new_client.get_file_properties()
        except ResourceNotFoundError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("file still exists after delete")
        print("[smoke] deleted file")

    finally:
        # 10. Delete filesystem ----------------------------------------- #
        try:
            service.delete_file_system(fs_name)
            print("[smoke] deleted filesystem")
        except ResourceNotFoundError:
            pass

    print("[smoke] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
