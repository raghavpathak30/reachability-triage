import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.scan.fetch import LocalDirFetcher, fetch_dists  # noqa: E402


class CountingFetcher(LocalDirFetcher):
    def __init__(self, d):
        super().__init__(d)
        self.downloads = 0

    def download(self, url, dest, **kw):
        self.downloads += 1
        super().download(url, dest, **kw)


def test_fetch_e1_wheels_and_extract_py_only(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    f = CountingFetcher(r["wheels"])
    res = fetch_dists([("langchain-openai", "1.1.13"), ("langchain-openai", "1.1.14")], f, tmp_path / "store")
    for version in ("1.1.13", "1.1.14"):
        fd = res.dists[("langchain-openai", version)]
        assert fd.status == "fetched"
        assert (fd.root / "langchain_openai" / "chat_models" / "base.py").is_file()
        files = [p for p in fd.root.rglob("*") if p.is_file()]
        assert all(p.suffix == ".py" or p.name == "RECORD" for p in files)
    assert f.downloads == 2
    assert res.gaps == []


def test_second_call_hits_store(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    f = CountingFetcher(r["wheels"])
    fetch_dists([("langchain-openai", "1.1.13")], f, tmp_path / "store")
    fetch_dists([("langchain-openai", "1.1.13")], f, tmp_path / "store")
    assert f.downloads == 1


def test_missing_wheel_is_no_wheel(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    res = fetch_dists([("langchain-openai", "9.9.9")], LocalDirFetcher(r["wheels"]), tmp_path / "store")
    assert res.dists[("langchain-openai", "9.9.9")].status == "no_wheel"
    assert res.gaps[0].kind == "no_wheel"
