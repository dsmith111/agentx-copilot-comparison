"""
Azure SDK smoke tests.

These tests run against a live emulator on port 10004.  They are automatically
skipped when the emulator is not reachable (e.g. during fast CI unit runs before
Docker starts).  The authoritative end-to-end SDK test is
`examples/python_sdk_smoke.py` executed by `scripts/evaluate.sh` after the
Docker container is up.
"""
from __future__ import annotations

import socket

import pytest

# ---------------------------------------------------------------------------
# SDK availability guard
# ---------------------------------------------------------------------------
pytest.importorskip(
    "azure.storage.filedatalake",
    reason="azure-storage-file-datalake not installed",
)

from azure.storage.filedatalake import DataLakeServiceClient  # noqa: E402
from azure.core.credentials import AzureNamedKeyCredential  # noqa: E402
from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ACCOUNT = "devstoreaccount1"
_ACCOUNT_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq"
    "/K1SZFPTOtr/KBHBeksoGMGw=="
)
_URL = f"http://127.0.0.1:10004/{_ACCOUNT}"
FS_NAME = "sdksmoke"


# ---------------------------------------------------------------------------
# Emulator availability check (module-level, evaluated once at collection)
# ---------------------------------------------------------------------------

def _emulator_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 10004), timeout=1.0):
            return True
    except OSError:
        return False


_SKIP = pytest.mark.skipif(
    not _emulator_reachable(),
    reason="Emulator not running on port 10004 - start with `docker compose up`",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _service() -> DataLakeServiceClient:
    cred = AzureNamedKeyCredential(_ACCOUNT, _ACCOUNT_KEY)
    return DataLakeServiceClient(account_url=_URL, credential=cred)


@pytest.fixture(autouse=True)
def cleanup():
    svc = _service()
    try:
        svc.delete_file_system(FS_NAME)
    except Exception:
        pass
    yield
    try:
        svc.delete_file_system(FS_NAME)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests  (all skipped when Docker isn't up)
# ---------------------------------------------------------------------------

@_SKIP
def test_sdk_full_lifecycle():
    """
    SDK-1: full lifecycle: create FS -> dir -> file -> append (2 chunks) ->
    flush -> download -> list -> rename -> delete -> ResourceNotFoundError.
    """
    svc = _service()
    fs_client = svc.create_file_system(FS_NAME)
    dir_client = fs_client.create_directory("mydir")
    file_client = dir_client.create_file("myfile.txt")

    chunk1, chunk2 = b"hello ", b"world"
    file_client.append_data(chunk1, offset=0, length=len(chunk1))
    file_client.append_data(chunk2, offset=len(chunk1), length=len(chunk2))
    file_client.flush_data(len(chunk1) + len(chunk2))

    content = file_client.download_file().readall()
    assert content == b"hello world", f"Got {content!r}"

    paths = list(fs_client.get_paths(recursive=True))
    names = [p.name for p in paths]
    assert any("mydir" in n for n in names)
    assert any("myfile.txt" in n for n in names)

    renamed = file_client.rename_file(f"{FS_NAME}/mydir/renamed.txt")

    with pytest.raises(ResourceNotFoundError):
        file_client.get_file_properties()

    renamed.delete_file()

    with pytest.raises(ResourceNotFoundError):
        renamed.download_file().readall()

    fs_client.delete_file_system()


@_SKIP
def test_sdk_resource_exists_error_on_if_none_match():
    """EC-3: Re-creating a file via SDK must raise ResourceExistsError."""
    svc = _service()
    fs_client = svc.create_file_system(FS_NAME)
    fs_client.get_file_client("exists.txt").create_file()
    with pytest.raises(ResourceExistsError):
        fs_client.get_file_client("exists.txt").create_file()


@_SKIP
def test_sdk_resource_not_found_after_delete():
    """EC-4: Deleted file must raise ResourceNotFoundError through SDK."""
    svc = _service()
    fs_client = svc.create_file_system(FS_NAME)
    fc = fs_client.get_file_client("todelete.txt")
    fc.create_file()
    fc.append_data(b"bye", offset=0)
    fc.flush_data(3)
    fc.delete_file()
    with pytest.raises(ResourceNotFoundError):
        fc.download_file().readall()


@_SKIP
def test_sdk_upload_data_overwrite_not_supported():
    """Known limitation: upload_data(overwrite=True) is not supported on existing files.

    Both DataLakeFileClient.create_file() and upload_data(overwrite=True) generate
    an identical PUT ?resource=file request with no If-None-Match header.  EC-3
    requires a second create_file() on the same path to raise ResourceExistsError,
    which means the emulator unconditionally rejects PUT ?resource=file when the path
    already exists.  There is no wire-level signal that distinguishes the two callers.

    Accepted design decision: upload_data is outside the required SDK lifecycle for
    this emulator.  Use delete_file() + create_file() + append_data() + flush_data()
    as the overwrite pattern instead.
    """
    svc = _service()
    fs_client = svc.create_file_system(FS_NAME)
    fc = fs_client.get_file_client("overwrite.txt")
    fc.create_file()
    fc.append_data(b"original", offset=0)
    fc.flush_data(8)
    assert fc.download_file().readall() == b"original"

    # upload_data(overwrite=True) is rejected because at the HTTP level it sends
    # the same request as a second create_file(), which must return 409 per EC-3.
    with pytest.raises(ResourceExistsError):
        fc.upload_data(b"new content", overwrite=True)

    # Workaround: delete then recreate.
    fc.delete_file()
    fc.create_file()
    fc.append_data(b"new content", offset=0)
    fc.flush_data(11)
    assert fc.download_file().readall() == b"new content"
