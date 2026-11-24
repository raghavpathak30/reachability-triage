from reachability.scan.advisories import Record
from reachability.scan.fixcommit import Commit, usable_commits

SHA = "a" * 40
PKG = ("PACKAGE", "https://github.com/Owner/Repo")


def rec(*refs, rid="X"):
    return Record(rid, (), (), references=tuple(refs))


def test_same_repo_commit_kept():
    r = rec(PKG, ("WEB", f"https://github.com/owner/repo/commit/{SHA}"))
    assert usable_commits([r]) == [Commit("owner", "repo", SHA)]


def test_other_repo_ignored():
    assert usable_commits([rec(PKG, ("WEB", f"https://github.com/other/repo/commit/{SHA}"))]) == []


def test_no_package_reference_no_commit():
    assert usable_commits([rec(("WEB", f"https://github.com/owner/repo/commit/{SHA}"))]) == []


def test_pull_url_ignored():
    assert usable_commits([rec(PKG, ("WEB", "https://github.com/owner/repo/pull/12"))]) == []


def test_case_insensitive_repo():
    r = rec(("PACKAGE", "https://github.com/owner/repo.git"), ("FIX", f"https://github.com/OWNER/REPO/commit/{SHA.upper()}"))
    assert usable_commits([r]) == [Commit("owner", "repo", SHA)]


def test_union_over_group():
    a = rec(PKG, ("WEB", f"https://github.com/owner/repo/commit/{SHA}"), rid="A")
    b = rec(PKG, ("WEB", f"https://github.com/owner/repo/commit/{'b' * 7}"), rid="B")
    assert [c.sha for c in usable_commits([a, b])] == [SHA, "b" * 7]


def test_any_reference_type():
    for t in ("WEB", "FIX", "REPORT", ""):
        assert usable_commits([rec(PKG, (t, f"https://github.com/owner/repo/commit/{SHA}"))])


def test_bad_sha_ignored():
    for sha in ("abc", "g" * 40, "a" * 41):
        assert usable_commits([rec(PKG, ("WEB", f"https://github.com/owner/repo/commit/{sha}"))]) == []
