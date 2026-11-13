import hashlib
import json
import socket
import zipfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan"
ADVISORY_DIR = FIXTURES / "advisories"
REFERENCE_SNAPSHOT_DEFAULT = "~/osv-snapshots/osv-pypi-2026-11-12.zip"


class NetworkBlockedError(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    raise NetworkBlockedError("network blocked in tests/scan")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture
def snapshot_factory(tmp_path):
    """Build `tmp_path/<name>` zip from fixture record ids and/or dicts.

    Each item is either an id string (read byte-for-byte from the advisory
    fixture dir) or a dict (serialised with its own "id").
    Returns (path, sha256).
    """

    def make(items, name="osv-pypi-2026-11-12.zip"):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as z:
            for item in items:
                if isinstance(item, str):
                    z.writestr(f"{item}.json", (ADVISORY_DIR / f"{item}.json").read_bytes())
                elif isinstance(item, tuple):
                    z.writestr(item[0], item[1])
                else:
                    z.writestr(f"{item['id']}.json", json.dumps(item))
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    return make


@pytest.fixture
def scan_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return root
