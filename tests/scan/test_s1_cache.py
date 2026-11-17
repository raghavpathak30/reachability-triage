import os
import shutil

import pytest

from reachability.scan import fetch as fetch_mod
from reachability.scan.fetch import LocalDirFetcher, fetch_dist
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import simple_lib_scan


class Counting(LocalDirFetcher):
    downloads = 0

    def download(self, *a, **k):
        type(self).downloads += 1
        super().download(*a, **k)


@pytest.fixture(autouse=True)
def _reset():
    Counting.downloads = 0


def _entry(store):
    return next(p for p in store.iterdir() if p.name.startswith("lib-1.0-"))


def test_hit_skips_download(tmp_path):
    s = simple_lib_scan(tmp_path)
    f = Counting(s["wheels"])
    fetch_dist("lib", "1.0", f, tmp_path / "st")
    fetch_dist("lib", "1.0", f, tmp_path / "st")
    assert Counting.downloads == 1


def test_gaps_restored_on_hit_so_results_match(tmp_path):
    bad = "x = " + "+".join(["1"] * 100000) + "\n"
    s = simple_lib_scan(tmp_path, extra_old={"lib/deep.py": bad})
    f = Counting(s["wheels"])
    cache = tmp_path / "cache"
    miss = run_scan(s["app"], s["snapshot"], s["sha256"], f, cache_dir=cache)
    n = Counting.downloads
    hit = run_scan(s["app"], s["snapshot"], s["sha256"], f, cache_dir=cache)
    assert Counting.downloads == n  # no new downloads
    assert [g.to_dict() for g in miss.gaps] == [g.to_dict() for g in hit.gaps]
    assert miss.closure_complete is hit.closure_complete is False
    assert [x.to_dict() for x in miss.findings] == [x.to_dict() for x in hit.findings]


def _prime(tmp_path):
    s = simple_lib_scan(tmp_path)
    f = Counting(s["wheels"])
    store = tmp_path / "st"
    fetch_dist("lib", "1.0", f, store)
    return f, store


def test_planted_symlink_forces_reextract(tmp_path):
    f, store = _prime(tmp_path)
    os.symlink("/etc/passwd", _entry(store) / "tree" / "lib" / "evil.py")
    fd, _ = fetch_dist("lib", "1.0", f, store)
    assert Counting.downloads == 2
    assert not (fd.root / "lib" / "evil.py").exists() and not os.path.islink(fd.root / "lib" / "evil.py")


def test_planted_txt_forces_reextract(tmp_path):
    f, store = _prime(tmp_path)
    (_entry(store) / "tree" / "notes.txt").write_text("x")
    fd, _ = fetch_dist("lib", "1.0", f, store)
    assert Counting.downloads == 2 and not (fd.root / "notes.txt").exists()


def test_missing_complete_marker_forces_reextract(tmp_path):
    f, store = _prime(tmp_path)
    (_entry(store) / "COMPLETE").unlink()
    fetch_dist("lib", "1.0", f, store)
    assert Counting.downloads == 2


def test_missing_gaps_record_forces_reextract(tmp_path):
    f, store = _prime(tmp_path)
    (_entry(store) / "gaps.json").unlink()
    fetch_dist("lib", "1.0", f, store)
    assert Counting.downloads == 2


def test_exception_leaves_no_partial_entry(tmp_path, monkeypatch):
    s = simple_lib_scan(tmp_path)
    f = Counting(s["wheels"])
    store = tmp_path / "st"

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(fetch_mod, "extract_wheel", boom)
    fd, gaps = fetch_dist("lib", "1.0", f, store)
    assert gaps[0].kind == "dist_error"
    assert not [p for p in store.iterdir()] if store.exists() else True


def test_entry_name_carries_rules_version(tmp_path):
    _, store = _prime(tmp_path)
    assert _entry(store).name.endswith(f"-r{fetch_mod.EXTRACT_RULES_VERSION}")
