#!/usr/bin/env python3
"""
Standalone ADLS Gen2 Lite Emulator smoke test.

Requires:
  - Emulator running on port 10004 (docker compose up)
  - azure-storage-file-datalake installed

Usage:
  python examples/python_sdk_smoke.py
"""
import sys

try:
    from azure.storage.filedatalake import DataLakeServiceClient
    from azure.core.credentials import AzureNamedKeyCredential
    from azure.core.exceptions import ResourceNotFoundError
except ImportError:
    print("ERROR: azure-storage-file-datalake not installed.")
    print("Run: pip install azure-storage-file-datalake==12.23.0")
    sys.exit(1)

ACCOUNT = "devstoreaccount1"
ACCOUNT_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq"
    "/K1SZFPTOtr/KBHBeksoGMGw=="
)
URL = f"http://127.0.0.1:10004/{ACCOUNT}"
FS = "smoketest"


def main() -> None:
    cred = AzureNamedKeyCredential(ACCOUNT, ACCOUNT_KEY)
    svc = DataLakeServiceClient(account_url=URL, credential=cred)

    # Cleanup from any previous run
    try:
        svc.delete_file_system(FS)
    except Exception:
        pass

    print("[1] Create filesystem")
    fs = svc.create_file_system(FS)

    print("[2] Create directory")
    d = fs.create_directory("mydir")

    print("[3] Create file")
    fc = d.create_file("hello.txt")

    print("[4] Append bytes (two chunks)")
    chunk1, chunk2 = b"hello ", b"world"
    fc.append_data(chunk1, offset=0, length=len(chunk1))
    fc.append_data(chunk2, offset=len(chunk1), length=len(chunk2))

    print("[5] Flush bytes")
    fc.flush_data(len(chunk1) + len(chunk2))

    print("[6] Download and verify")
    content = fc.download_file().readall()
    assert content == b"hello world", f"Got {content!r}"
    print(f"    content = {content!r}  [OK]")

    print("[7] List paths")
    paths = list(fs.get_paths(recursive=True))
    names = [p.name for p in paths]
    assert any("mydir" in n for n in names), f"mydir not found: {names}"
    assert any("hello.txt" in n for n in names), f"hello.txt not found: {names}"
    print(f"    paths = {names}  [OK]")

    print("[8] Rename file")
    renamed = fc.rename_file(f"{FS}/mydir/renamed.txt")

    print("[9] Verify old path is missing")
    try:
        fc.get_file_properties()
        raise AssertionError("Expected ResourceNotFoundError for old path")
    except ResourceNotFoundError:
        print("    old path 404  [OK]")

    print("[10] Delete renamed file")
    renamed.delete_file()

    print("[11] Verify deleted file raises ResourceNotFoundError")
    try:
        renamed.download_file().readall()
        raise AssertionError("Expected ResourceNotFoundError")
    except ResourceNotFoundError:
        print("    ResourceNotFoundError raised  [OK]")

    print("[12] Delete filesystem")
    fs.delete_file_system()

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
