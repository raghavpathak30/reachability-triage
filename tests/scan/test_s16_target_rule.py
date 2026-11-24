from reachability.scan.targets import named_names, resolve_targets
from tests.scan.test_s15_target_confidence import _setup

OLD = "def f():\n    return 1\n\n\ndef g():\n    return 1\n"
NEW = "def f():\n    return 2\n\n\ndef g():\n    return 2\n"
F = ("p.a", "f", 0)
G = ("p.a", "g", 0)


def targets(tmp_path, text, fix=frozenset(), commits=()):
    c, inst, ch = _setup(tmp_path, OLD, NEW)
    try:
        ts, _ = resolve_targets(ch, inst, c, "d", named_names(text), fix, commits)
    finally:
        c.close()
    return {t.qualname: t for t in ts}


def test_high_needs_named_and_fix_changed(tmp_path):
    ts = targets(tmp_path, "`f()`", frozenset({F}), ("abc1234",))
    assert ts["f"].confidence == "high" and ts["f"].in_fix_commit and ts["f"].fix_commits == ("abc1234",)


def test_named_release_changed_not_in_fix_is_medium(tmp_path):
    ts = targets(tmp_path, "`f()`", frozenset({G}))
    assert ts["f"].confidence == "medium" and not ts["f"].in_fix_commit


def test_no_commit_no_high(tmp_path):
    assert all(t.confidence == "medium" for t in targets(tmp_path, "`f()` `g()`").values())


def test_fix_changed_unnamed_is_medium(tmp_path):
    ts = targets(tmp_path, "nothing", frozenset({F}))
    assert ts["f"].confidence == "medium" and ts["f"].in_fix_commit is True


def test_target_json_has_fix_fields(tmp_path):
    d = targets(tmp_path, "`f()`", frozenset({F}), ("abc1234",))["f"].to_dict()
    assert d["fix_commits"] == ["abc1234"] and d["in_fix_commit"] is True
