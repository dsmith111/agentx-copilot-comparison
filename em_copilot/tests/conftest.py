import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import pytest
from fastapi.testclient import TestClient

from adls_lite.app import create_app
from adls_lite.store import Store


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def persistent_store(tmp_path):
    return Store(root=str(tmp_path))


@pytest.fixture
def client(store):
    app = create_app(store=store)
    with TestClient(app) as c:
        yield c
