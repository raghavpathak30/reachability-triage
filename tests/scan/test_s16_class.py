from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import run_scan
from reachability.scan.targets import named_names, resolve_targets
from tests.scan.s1_support import FIX_KEY, advisory, lib_files, make_scan
from tests.scan.test_s15_target_confidence import _setup

OLD = "class C:\n    k = 1\n\n    def m(self):\n        return 1\n"
NEW = "class C:\n    k = 2\n\n    def m(self):\n        return 1\n"
CLASS_DIFF = (
    "diff --git a/lib/core.py b/lib/core.py\n--- a/lib/core.py\n+++ b/lib/core.py\n"
    "@@ -1,3 +1,3 @@\n class C:\n-    k = 1\n+    k = 2\n \n"
)
APP = "from lib.core import C\n\n\ndef main():\n    return C()\n\n\nif __name__ == '__main__':\n    main()\n"


def _scan(tmp_path):
    return make_scan(
        tmp_path, app_files={"main.py": APP}, pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(OLD), ("lib", "2.0"): lib_files(NEW)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib", details="The class `C` is vulnerable; `C()` misbehaves.")],
        fix_diffs={FIX_KEY: CLASS_DIFF},
    )


def test_class_named_in_text_is_no_target(tmp_path):
    c, inst, ch = _setup(tmp_path, OLD, NEW)
    try:
        ts, _ = resolve_targets(ch, inst, c, "d", named_names("`C` and `C()`"), frozenset({("p.a", "C", 0)}))
    finally:
        c.close()
    assert [t.qualname for t in ts] == []


def test_app_calls_class_never_path_proven_to_class_node(tmp_path):
    s = _scan(tmp_path)
    res = run_scan(s["app"], s["snapshot"], s["sha256"], LocalDirFetcher(s["wheels"]))
    (f,) = res.findings
    assert f.evidence_level is None and f.verdict == "unknown"
    assert all(not t.node_id.endswith(":C") for t in f.targets)


def test_class_body_change_makes_no_target(tmp_path):
    c, inst, ch = _setup(tmp_path, OLD, NEW)
    try:
        assert ch  # the release diff does see the class change
        ts, _ = resolve_targets(ch, inst, c, "d", named_names(""), frozenset())
    finally:
        c.close()
    assert ts == []


def test_method_ordinals_unchanged(tmp_path):
    c, inst, ch = _setup(tmp_path, OLD, NEW)
    try:
        assert ("p.a", "C.m", 0) in inst.defs and ("p.a", "C", 0) in inst.defs
    finally:
        c.close()
