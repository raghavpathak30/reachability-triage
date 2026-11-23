import os

import pytest

from reachability.scan.fixcommit import (
    Commit, DiffStore, DiffUnavailable, LocalDiffDir, gap_for, safe_path,
)

SHA = "a" * 40
C = Commit("owner", "repo", SHA)
DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"


class Fake:
    def __init__(self, text=DIFF, exc=None):
        self.text, self.exc, self.calls = text, exc, 0

    def fetch_diff(self, owner, repo, sha):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.text


def put(root, text=DIFF, sha=SHA):
    p = root / "owner" / "repo" / f"{sha}.diff"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode() if isinstance(text, str) else text)
    return p


def test_local_dir_serves_diff(tmp_path):
    put(tmp_path)
    assert LocalDiffDir(tmp_path).fetch_diff("owner", "repo", SHA) == DIFF


def test_missing_diff_is_gap(tmp_path):
    store = DiffStore(tmp_path / "c", LocalDiffDir(tmp_path / "none"))
    text, reason = store.get(C)
    assert text is None and reason == "no local diff"
    assert gap_for("lib", C, reason).detail == f"owner/repo@{SHA}: no local diff"


def test_cache_written_then_hit(tmp_path):
    f = Fake()
    DiffStore(tmp_path, f).get(C)
    assert (tmp_path / "fix-commits/owner/repo" / f"{SHA}.diff").read_text() == DIFF
    s2 = DiffStore(tmp_path, f)
    assert s2.get(C) == (DIFF, None) and f.calls == 1 and s2.cached == 1


def test_decode_error_is_gap(tmp_path):
    put(tmp_path, b"\xff\xfe\x00bad")
    text, reason = DiffStore(tmp_path / "c", LocalDiffDir(tmp_path)).get(C)
    assert text is None and reason == "decode error"


def test_no_source_configured_is_gap(tmp_path):
    assert DiffStore(tmp_path, None).get(C) == (None, "no diff source")


def test_unreferenced_local_diff_ignored(tmp_path):
    put(tmp_path, sha="b" * 40)
    other = Commit("owner", "repo", "c" * 40)
    assert DiffStore(tmp_path / "c", LocalDiffDir(tmp_path)).get(other)[0] is None


def test_dotdot_owner_repo_refused(tmp_path):
    for o, r in (("..", "repo"), ("owner", ".."), ("owner", ".hidden"), ("a/b", "r")):
        with pytest.raises(DiffUnavailable):
            safe_path(tmp_path, o, r, SHA)
        with pytest.raises(DiffUnavailable):
            LocalDiffDir(tmp_path).fetch_diff(o, r, SHA)
        text, reason = DiffStore(tmp_path / "c", Fake()).get(Commit(o, r, SHA))
        assert text is None and reason == "invalid owner/repo/sha"


def test_symlink_in_local_dir_refused(tmp_path):
    real = tmp_path / "real.diff"
    real.write_text(DIFF)
    d = tmp_path / "d" / "owner" / "repo"
    d.mkdir(parents=True)
    os.symlink(real, d / f"{SHA}.diff")
    with pytest.raises(DiffUnavailable):
        LocalDiffDir(tmp_path / "d").fetch_diff("owner", "repo", SHA)


def test_corrupt_cache_refetched(tmp_path):
    p = put(tmp_path / "fix-commits", "garbage")
    f = Fake()
    assert DiffStore(tmp_path, f).get(C) == (DIFF, None) and f.calls == 1
    assert p.read_text() == DIFF


def test_cache_write_atomic(tmp_path):
    DiffStore(tmp_path, Fake()).get(C)
    names = os.listdir(tmp_path / "fix-commits/owner/repo")
    assert names == [f"{SHA}.diff"]


def test_not_a_diff_and_exceptions_are_status_classes(tmp_path):
    assert DiffStore(tmp_path, Fake("hello")).get(C)[1] == "not a diff"

    class E(Exception):
        code = 403

    assert DiffStore(tmp_path / "x", Fake(exc=E("<html>secret body</html>"))).get(C)[1] == "HTTP 403"
    assert DiffStore(tmp_path / "y", Fake(exc=TimeoutError())).get(C)[1] == "timeout"
    assert DiffStore(tmp_path / "z", Fake(exc=RuntimeError("x"))).get(C)[1] == "error: RuntimeError"


def test_gap_reason_is_status_class_not_body(tmp_path):
    g = gap_for("lib", C, "HTTP 403\x1b[31m" + "x" * 500)
    assert "\x1b" not in g.detail and len(g.detail) <= 200 and g.kind == "fix_commit_unavailable"


def test_diff_budget(tmp_path):
    s = DiffStore(tmp_path, Fake(), max_diffs=1)
    s.get(C)
    assert s.get(Commit("owner", "repo", "d" * 40)) == (None, "diff budget: 1")
    t = [0.0]

    def clock():
        t[0] += 400
        return t[0]

    s = DiffStore(tmp_path / "t", Fake(), clock=clock)
    s.get(C)
    assert s.get(Commit("owner", "repo", "e" * 40))[1] == "diff budget: 300 s"
