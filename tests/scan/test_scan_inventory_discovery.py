import os

from reachability.scan.inventory import discover_lockfile


def touch(root, *names):
    for n in names:
        (root / n).write_text("junk that is never parsed\n")


def test_uv_lock_wins_over_poetry_and_requirements_others_ignored(scan_root):
    touch(scan_root, "uv.lock", "poetry.lock", "requirements.txt")
    kind, path, skipped = discover_lockfile(scan_root.resolve())
    assert kind == "uv.lock"
    assert path == scan_root.resolve() / "uv.lock"
    assert [(s.kind, s.path) for s in skipped] == [
        ("ignored_lockfile", "poetry.lock"),
        ("ignored_lockfile", "requirements.txt"),
    ]
    assert all("lower precedence than uv.lock" in s.detail for s in skipped)


def test_poetry_wins_over_requirements(scan_root):
    touch(scan_root, "poetry.lock", "requirements.txt")
    kind, _, skipped = discover_lockfile(scan_root.resolve())
    assert kind == "poetry.lock"
    assert [s.path for s in skipped] == ["requirements.txt"]


def test_requirements_only(scan_root):
    touch(scan_root, "requirements.txt")
    kind, path, skipped = discover_lockfile(scan_root.resolve())
    assert (kind, skipped) == ("requirements.txt", [])
    assert path.name == "requirements.txt"


def test_pipfile_lock_and_pylock_recorded_as_ignored_a1b(scan_root):
    touch(scan_root, "requirements.txt", "Pipfile.lock", "pylock.toml")
    kind, _, skipped = discover_lockfile(scan_root.resolve())
    assert kind == "requirements.txt"
    assert sorted((s.kind, s.path, s.detail) for s in skipped) == [
        ("ignored_lockfile", "Pipfile.lock", "format arrives in A1b"),
        ("ignored_lockfile", "pylock.toml", "format arrives in A1b"),
    ]


def test_symlinked_uv_lock_skipped_falls_to_next(tmp_path, scan_root):
    (tmp_path / "real.lock").write_text("x")
    os.symlink(tmp_path / "real.lock", scan_root / "uv.lock")
    touch(scan_root, "requirements.txt")
    kind, _, skipped = discover_lockfile(scan_root.resolve())
    assert kind == "requirements.txt"
    assert [(s.kind, s.path) for s in skipped] == [("symlink", "uv.lock")]


def test_no_lockfile_found(scan_root):
    assert discover_lockfile(scan_root.resolve()) == (None, None, [])


def test_pipfile_only_means_no_lockfile(scan_root):
    touch(scan_root, "Pipfile.lock")
    kind, path, skipped = discover_lockfile(scan_root.resolve())
    assert (kind, path) == (None, None)
    assert [s.kind for s in skipped] == ["ignored_lockfile"]
