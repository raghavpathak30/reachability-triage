from pathlib import Path

import pytest

from reachability.scan.inventory import (
    LockfileParseError,
    UvLockFormatError,
    UvLockRootError,
    build_inventory,
    find_uv_root,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan" / "lockfiles"


def pkg(name, source, **extra):
    return {"name": name, "version": "1.0", "source": source, **extra}


def test_root_is_editable_dot():
    root = find_uv_root([pkg("a", {"registry": "x"}), pkg("app", {"editable": "."})])
    assert root["name"] == "app"


def test_root_is_virtual_dot():
    assert find_uv_root([pkg("app", {"virtual": "."})])["name"] == "app"


def test_registry_package_never_root():
    with pytest.raises(UvLockRootError):
        find_uv_root([pkg("a", {"registry": "https://pypi.org/simple"})])


def test_directory_source_dot_is_not_root():
    with pytest.raises(UvLockRootError):
        find_uv_root([pkg("a", {"directory": "."})])


def test_workspace_member_editable_subdir_is_not_root():
    with pytest.raises(UvLockRootError, match="packages/ws"):
        find_uv_root([pkg("ws", {"editable": "packages/ws"})])


def test_no_root_raises_uvlockrooterror_naming_candidates():
    with pytest.raises(UvLockRootError) as ei:
        build_inventory(FIX / "uv_no_root")
    msg = str(ei.value)
    assert "root" in msg and "ws-member" in msg and "packages/ws-member" in msg


def test_two_roots_raises_uvlockrooterror_naming_both():
    with pytest.raises(UvLockRootError) as ei:
        build_inventory(FIX / "uv_two_roots")
    msg = str(ei.value)
    assert "root" in msg and "app-one" in msg and "app-two" in msg


def test_root_in_subdirectory_is_refused_and_named():
    with pytest.raises(UvLockRootError) as ei:
        build_inventory(FIX / "uv_root_subdir")
    assert "backend" in str(ei.value) and "app" in str(ei.value)


def write_uv(root, body):
    (root / "uv.lock").write_text(body)
    return build_inventory(root)


def test_uv_lock_unknown_version_raises(scan_root):
    with pytest.raises(UvLockFormatError, match="version"):
        write_uv(scan_root, 'version = 2\n[[package]]\nname="a"\nversion="1"\nsource={editable="."}\n')


@pytest.mark.parametrize("body", ['[[package]]\nname="a"\nversion="1"\nsource={editable="."}\n', 'version = "1"\n', "version = true\n"])
def test_uv_lock_missing_or_non_integer_version_raises(scan_root, body):
    with pytest.raises(UvLockFormatError):
        write_uv(scan_root, body)


def test_uv_lock_malformed_toml_raises_parse_error(scan_root):
    with pytest.raises(LockfileParseError):
        write_uv(scan_root, "version = [")


from reachability.scan.inventory import Status, UvLockResolutionError  # noqa: E402


def uv_entries(name):
    inv = build_inventory(FIX / name)
    return {e.dist: e for e in inv.entries}


def test_closure_follows_dependencies_transitively():
    d = uv_entries("uv_basic")
    assert {k: (v.version, v.status) for k, v in d.items() if v.status is Status.PINNED} == {
        "flask": ("3.0.3", Status.PINNED),
        "click": ("8.1.7", Status.PINNED),
        "jinja2": ("3.1.4", Status.PINNED),
        "markupsafe": ("2.1.5", Status.PINNED),
    }


def test_dev_dependencies_excluded():
    assert "pytest" not in uv_entries("uv_basic")


def test_root_not_listed():
    assert "myapp" not in uv_entries("uv_basic")
    assert "app" not in uv_entries("uv_extras_markers")


def test_unreachable_package_not_listed():
    assert "orphan" not in uv_entries("uv_basic")


def test_edge_marker_false_not_traversed_and_target_marked_marker_skipped():
    e = uv_entries("uv_basic")["colorama"]
    assert (e.version, e.status) == ("0.4.6", Status.MARKER_SKIPPED)
    assert e.line is None and e.source_file == "uv.lock"


def test_edge_marker_true_traversed(scan_root):
    body = (
        'version = 1\n[[package]]\nname="app"\nversion="0"\nsource={editable="."}\n'
        'dependencies=[{name="a", marker="sys_platform == \'linux\'"}]\n'
        '[[package]]\nname="a"\nversion="1.0"\nsource={registry="https://pypi.org/simple"}\n'
    )
    (scan_root / "uv.lock").write_text(body)
    inv = build_inventory(scan_root)
    assert [(e.dist, e.status) for e in inv.entries] == [("a", Status.PINNED)]


def test_false_edge_before_true_edge_package_stays_pinned():
    e = uv_entries("uv_extras_markers")["pywin"]
    assert (e.version, e.status) == ("306", Status.PINNED)


def test_edge_extra_follows_optional_dependencies_of_target_only_for_requested_extra():
    d = uv_entries("uv_extras_markers")
    assert d["uvicorn"].status is Status.PINNED
    assert "not-requested" not in d


def test_root_optional_dependencies_not_followed():
    assert "root-opt-only" not in uv_entries("uv_extras_markers")


def test_edge_back_to_root_with_extra_ignored():
    inv = build_inventory(FIX / "uv_extras_markers")
    assert "root-opt-only" not in {e.dist for e in inv.entries}
    assert any("edge to root" in n for n in inv.notes)


def test_git_source_package_unsupported():
    e = uv_entries("uv_extras_markers")["gitdep"]
    assert e.status is Status.UNSUPPORTED and e.version == "0.1.0"
    assert any("git" in n for n in e.notes)


def test_package_without_version_unsupported():
    e = uv_entries("uv_extras_markers")["wheelless"]
    assert (e.version, e.status) == (None, Status.UNSUPPORTED)
    assert any("no version in lock" in n for n in e.notes)


def test_false_marker_python_full_version_edge():
    assert uv_entries("uv_extras_markers")["oldpy"].status is Status.MARKER_SKIPPED


def test_cycle_terminates():
    d = uv_entries("uv_cycle")
    assert set(d) == {"a", "b"}


def two_versions(scan_root, edge):
    body = (
        'version = 1\n[[package]]\nname="app"\nversion="0"\nsource={editable="."}\n'
        f"dependencies=[{edge}]\n"
        '[[package]]\nname="a"\nversion="1.0"\nsource={registry="https://pypi.org/simple"}\n'
        '[[package]]\nname="a"\nversion="2.0"\nsource={registry="https://pypi.org/simple"}\n'
    )
    (scan_root / "uv.lock").write_text(body)
    return build_inventory(scan_root)


def test_ambiguous_edge_without_version_raises_resolution_error(scan_root):
    with pytest.raises(UvLockResolutionError, match="ambiguous"):
        two_versions(scan_root, '{name="a"}')


def test_edge_with_version_resolves_ambiguity(scan_root):
    inv = two_versions(scan_root, '{name="a", version="2.0"}')
    assert [(e.dist, e.version) for e in inv.entries] == [("a", "2.0")]


def test_unresolved_edge_raises_resolution_error(scan_root):
    with pytest.raises(UvLockResolutionError, match="no package"):
        two_versions(scan_root, '{name="nope"}')


def test_edge_name_is_canonicalised(scan_root):
    body = (
        'version = 1\n[[package]]\nname="app"\nversion="0"\nsource={editable="."}\n'
        'dependencies=[{name="Zope.Interface"}]\n'
        '[[package]]\nname="zope-interface"\nversion="6.0"\nsource={registry="https://pypi.org/simple"}\n'
    )
    (scan_root / "uv.lock").write_text(body)
    assert [e.dist for e in build_inventory(scan_root).entries] == ["zope-interface"]


def test_uv_lock_wins_end_to_end_over_requirements(scan_root):
    (scan_root / "requirements.txt").write_text("other==1.0\n")
    (scan_root / "poetry.lock").write_text('[[package]]\nname="p"\nversion="1"\n')
    (scan_root / "uv.lock").write_text(
        'version = 1\n[[package]]\nname="app"\nversion="0"\nsource={editable="."}\n'
        'dependencies=[{name="a"}]\n[[package]]\nname="a"\nversion="1.0"\nsource={registry="https://pypi.org/simple"}\n'
    )
    inv = build_inventory(scan_root)
    assert inv.lockfile == "uv.lock"
    assert [e.dist for e in inv.entries] == ["a"]
    assert sorted(s.path for s in inv.skipped if s.kind == "ignored_lockfile") == ["poetry.lock", "requirements.txt"]
