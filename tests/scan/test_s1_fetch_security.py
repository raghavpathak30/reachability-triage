import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.scan.fetch import FILES_HOST, LocalDirFetcher, PyPIFetcher, fetch_dist  # noqa: E402
from reachability.scan.lock_wheels import LockEntry  # noqa: E402
from reachability.scan.wheel_select import Candidate  # noqa: E402
from tests.scan.test_s1_pypi_fetcher import FakeResp  # noqa: E402


def _lock(url, sha="aa"):
    return LockEntry("https://pypi.org/simple", (Candidate("p-1-py3-none-any.whl", url, sha),), False)


@pytest.mark.parametrize(
    "url,needle",
    [
        ("https://evil.example/p-1-py3-none-any.whl", "evil.example"),
        ("http://files.pythonhosted.org/p-1-py3-none-any.whl", "only https"),
        ("https://files.pythonhosted.org.evil.example/p.whl", "files.pythonhosted.org.evil.example"),
    ],
)
def test_lock_url_host_refused_and_nothing_downloaded(tmp_path, url, needle):
    class Rec(LocalDirFetcher):
        def download(self, *a, **k):
            raise AssertionError("must not download")

    fd, gaps = fetch_dist("p", "1", Rec(tmp_path), tmp_path / "s", lock=_lock(url))
    assert fd.status == "no_wheel" and needle in fd.reason and gaps[0].kind == "no_wheel"
    assert not (tmp_path / "s").exists() or not any((tmp_path / "s").iterdir())


class Seq:
    """Fake opener answering the JSON request, then the wheel download."""

    def __init__(self, meta: dict, wheel: bytes):
        self.meta, self.wheel = meta, wheel

    def open(self, req, timeout=None):
        url = req.full_url
        if url.endswith("/json"):
            return FakeResp(json.dumps(self.meta).encode(), url)
        return FakeResp(self.wheel, url)


def _wheel_bytes(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    return (r["wheels"] / "langchain_openai-1.1.13-py3-none-any.whl").read_bytes()


def _meta(digest):
    return {
        "urls": [
            {
                "filename": "langchain_openai-1.1.13-py3-none-any.whl",
                "url": f"https://{FILES_HOST}/packages/langchain_openai-1.1.13-py3-none-any.whl",
                "packagetype": "bdist_wheel",
                "digests": {"sha256": digest},
            }
        ]
    }


def test_digest_mismatch_never_opens_archive(tmp_path, monkeypatch):
    data = _wheel_bytes(tmp_path)

    def boom(*a, **k):
        raise AssertionError("archive opened before sha256 verification")

    monkeypatch.setattr(zipfile, "ZipFile", boom)
    fd, gaps = fetch_dist(
        "langchain-openai", "1.1.13", PyPIFetcher(Seq(_meta("00" * 32), data)), tmp_path / "store"
    )
    assert fd.status == "no_wheel" and "sha256 mismatch" in fd.reason
    assert not list((tmp_path / "store").rglob("*.py"))


def test_matching_digest_extracts(tmp_path):
    data = _wheel_bytes(tmp_path)
    fd, gaps = fetch_dist(
        "langchain-openai", "1.1.13", PyPIFetcher(Seq(_meta(hashlib.sha256(data).hexdigest()), data)), tmp_path / "store"
    )
    assert fd.status == "fetched" and (fd.root / "langchain_openai" / "chat_models" / "base.py").is_file()


def test_lock_hash_mismatch_never_extracts(tmp_path, monkeypatch):
    data = _wheel_bytes(tmp_path)
    monkeypatch.setattr(zipfile, "ZipFile", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    lock = _lock(f"https://{FILES_HOST}/packages/langchain_openai-1.1.13-py3-none-any.whl", "11" * 32)
    fd, _ = fetch_dist("langchain-openai", "1.1.13", PyPIFetcher(Seq({}, data)), tmp_path / "store", lock=lock)
    assert fd.status == "no_wheel" and "sha256 mismatch" in fd.reason
