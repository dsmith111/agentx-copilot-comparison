"""Root conftest: add src/ to sys.path so adls_lite is importable without install."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
