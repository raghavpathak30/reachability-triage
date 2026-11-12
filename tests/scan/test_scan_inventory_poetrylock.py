from pathlib import Path

import pytest

from reachability.scan.inventory import LockfileParseError, Status, build_inventory

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan" / "lockfiles"


def entries(name):
    return {e.dist: e for e in build_inventory(FIX / name).entries}


def test_poetry_all_packages_listed_with_groups():
    d = entries("poetry_basic")
    assert set(d) == {"click", "pytest", "legacy-cat", "gitdep", "privdep", "weird"}
    assert d["click"].groups == ("main",)
    assert d["pytest"].groups == ("dev",)
    assert d["click"].source_file == "poetry.lock"
    assert d["click"].line is None


def test_poetry_legacy_category_used_when_groups_absent():
    assert entries("poetry_basic")["legacy-cat"].groups == ("main",)


def test_poetry_git_source_unsupported():
    e = entries("poetry_basic")["gitdep"]
    assert e.status is Status.UNSUPPORTED and e.version == "0.1.0"
    assert any("git" in n for n in e.notes)


def test_poetry_legacy_source_unsupported():
    e = entries("poetry_basic")["privdep"]
    assert e.status is Status.UNSUPPORTED
    assert any("legacy" in n for n in e.notes)


def test_poetry_non_pep440_version_unsupported():
    e = entries("poetry_basic")["weird"]
    assert e.status is Status.UNSUPPORTED
    assert any("1.0-custom" in n for n in e.notes)


def test_poetry_string_marker_false_gives_marker_skipped():
    assert entries("poetry_markers")["colorama"].status is Status.MARKER_SKIPPED
    assert entries("poetry_markers")["uvloop"].status is Status.PINNED


def test_poetry_table_markers_all_false_gives_marker_skipped_but_one_true_keeps():
    d = entries("poetry_markers")
    assert d["allfalse"].status is Status.MARKER_SKIPPED
    assert d["grouped"].status is Status.PINNED
    assert d["partial"].status is Status.PINNED  # dev has no marker entry: counts as true


def write_poetry(root, body):
    (root / "poetry.lock").write_text(body)
    return build_inventory(root)


def test_poetry_package_without_version_unsupported(scan_root):
    inv = write_poetry(scan_root, '[[package]]\nname = "nov"\ngroups = ["main"]\n')
    e = inv.entries[0]
    assert (e.dist, e.version, e.status) == ("nov", None, Status.UNSUPPORTED)
    assert any("no version" in n for n in e.notes)


def test_poetry_malformed_toml_raises_lockfile_parse_error(scan_root):
    with pytest.raises(LockfileParseError, match="poetry.lock"):
        write_poetry(scan_root, "[[package\nname = ")


def test_poetry_package_source_pypi_is_normal(scan_root):
    inv = write_poetry(
        scan_root,
        '[[package]]\nname = "x"\nversion = "1.0"\n[package.source]\ntype = "pypi"\n',
    )
    assert inv.entries[0].status is Status.PINNED


def test_poetry_optional_is_only_a_note(scan_root):
    inv = write_poetry(scan_root, '[[package]]\nname = "x"\nversion = "1.0"\noptional = true\n')
    assert inv.entries[0].status is Status.PINNED
    assert any("optional" in n for n in inv.entries[0].notes)


def test_poetry_duplicate_name_two_versions_kept(scan_root):
    body = (
        '[[package]]\nname = "x"\nversion = "1.0"\nmarkers = "python_version < \\"3.9\\""\n'
        '[[package]]\nname = "x"\nversion = "2.0"\nmarkers = "python_version >= \\"3.9\\""\n'
    )
    inv = write_poetry(scan_root, body)
    assert [(e.version, e.status) for e in inv.entries] == [("1.0", Status.MARKER_SKIPPED), ("2.0", Status.PINNED)]


def test_poetry_invalid_marker_raises_parse_error(scan_root):
    with pytest.raises(LockfileParseError):
        write_poetry(scan_root, '[[package]]\nname = "x"\nversion = "1"\nmarkers = "((("\n')


def test_poetry_requirements_txt_is_ignored_when_poetry_present(scan_root):
    (scan_root / "requirements.txt").write_text("other==1.0\n")
    inv = write_poetry(scan_root, '[[package]]\nname = "x"\nversion = "1.0"\n')
    assert [e.dist for e in inv.entries] == ["x"]
    assert [(s.kind, s.path) for s in inv.skipped] == [("ignored_lockfile", "requirements.txt")]
