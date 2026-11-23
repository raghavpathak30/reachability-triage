from reachability.scan.fixcommit import parse_diff

D = """diff --git a/src/pkg/mod.py b/src/pkg/mod.py
index 1..2 100644
--- a/src/pkg/mod.py
+++ b/src/pkg/mod.py
@@ -1,3 +1,3 @@ class X:
 a
-b
+c
 d
\\ No newline at end of file
diff --git a/tests/test_mod.py b/tests/test_mod.py
--- a/tests/test_mod.py
+++ b/tests/test_mod.py
@@ -1 +1 @@
-x
+y
diff --git a/docs/conf.py b/docs/conf.py
--- a/docs/conf.py
+++ b/docs/conf.py
@@ -1 +1 @@
-x
+y
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-x
+y
"""


def test_parse_hunks_both_sides():
    (f,) = parse_diff(D)
    assert f.path == "src/pkg/mod.py" and len(f.hunks) == 1
    assert f.hunks[0].removed_side == ["a", "b", "d"]
    assert f.hunks[0].added_side == ["a", "c", "d"]


def test_skips_tests_docs_and_non_py():
    assert [f.path for f in parse_diff(D)] == ["src/pkg/mod.py"]


def test_no_newline_marker_handled():
    assert len(parse_diff(D)[0].hunks) == 1


def test_hunk_count_mismatch_dropped():
    c: dict = {}
    bad = D.replace("@@ -1,3 +1,3 @@", "@@ -1,2 +1,3 @@")
    assert parse_diff(bad, c)[0].hunks == () and c["bad_header"] == 1


def test_truncated_diff_drops_last_hunk():
    c: dict = {}
    cut = D.split("\\ No newline")[0]
    cut = cut.rsplit(" d\n", 1)[0]
    (f,) = parse_diff(cut, c)
    assert f.hunks == () and c["truncated"] == 1


def test_rename_with_edit_skipped():
    r = "diff --git a/a.py b/b.py\n--- a/a.py\n+++ b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert parse_diff(r) == []


def test_new_and_deleted_files_skipped():
    r = "diff --git a/a.py b/a.py\n--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+y\n"
    assert parse_diff(r) == []


def test_default_counts_are_one():
    r = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -3 +3 @@\n-x\n+y\n"
    assert parse_diff(r)[0].hunks[0].items == (("-", "x"), ("+", "y"))
