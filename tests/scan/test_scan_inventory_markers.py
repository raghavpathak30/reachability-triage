import pytest
from packaging import markers as pk_markers
from packaging.markers import Marker

from reachability.scan import inventory
from reachability.scan.inventory import MARKER_ENV, TARGET_ENV, Status, build_inventory, marker_applies


def one(root, line):
    (root / "requirements.txt").write_text(line + "\n")
    return build_inventory(root).entries[0]


def test_marker_env_has_exactly_the_eight_keys_and_values():
    assert TARGET_ENV == "cp313-manylinux_x86_64"
    assert MARKER_ENV == {
        "python_version": "3.13",
        "python_full_version": "3.13.15",
        "sys_platform": "linux",
        "platform_system": "Linux",
        "platform_machine": "x86_64",
        "os_name": "posix",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
    }


def test_false_marker_gives_marker_skipped(scan_root):
    e = one(scan_root, 'pywin32==306; sys_platform == "win32"')
    assert (e.dist, e.version, e.status) == ("pywin32", "306", Status.MARKER_SKIPPED)
    assert any("marker" in n for n in e.notes)


def test_true_marker_keeps_pinned(scan_root):
    assert one(scan_root, 'uvloop==0.19.0; sys_platform != "win32"').status is Status.PINNED


def test_python_full_version_marker(scan_root):
    assert one(scan_root, 'foo==1.0; python_full_version >= "3.13.6"').status is Status.PINNED
    assert one(scan_root, 'foo==1.0; python_full_version < "3.13.6"').status is Status.MARKER_SKIPPED


def test_extra_is_empty_string(scan_root):
    assert one(scan_root, 'foo==1.0; extra == "x"').status is Status.MARKER_SKIPPED


def test_marker_false_on_unpinned_line_is_marker_skipped(scan_root):
    assert one(scan_root, 'foo>=1; sys_platform == "win32"').status is Status.MARKER_SKIPPED


@pytest.mark.parametrize(
    "marker",
    [
        'platform_release == "6.1.0"',
        '"android" in platform_release',
        '"android" not in platform_release',
        'platform_version >= "1"',
        'implementation_version >= "3.13"',
    ],
)
def test_marker_unmapped_key_kept_with_note(marker):
    ok, notes = marker_applies(Marker(marker))
    assert ok is True
    assert len(notes) == 1 and notes[0].startswith("marker uses unmapped key ") and notes[0].endswith("; kept")


def test_unmapped_key_inside_nested_or_is_found():
    ok, notes = marker_applies(Marker('sys_platform == "win32" and (python_version >= "3" or platform_release == "1")'))
    assert ok is True and notes


@pytest.mark.parametrize(
    "marker, expected",
    [
        ('sys_platform == "linux"', True),
        ('sys_platform == "win32"', False),
        ('os_name == "nt"', False),
        ('os.name == "posix"', True),
        ('sys.platform == "linux"', True),
        ('platform_machine == "arm64"', False),
        ('platform_system == "Linux"', True),
        ('implementation_name == "cpython"', True),
        ('platform_python_implementation == "PyPy"', False),
        ('python_version >= "3.13"', True),
        ('python_version < "3.13"', False),
        ('sys_platform == "linux" and python_version < "3.9"', False),
    ],
)
def test_marker_mapped_key_evaluation(marker, expected):
    ok, notes = marker_applies(Marker(marker))
    assert ok is expected
    assert notes == ()


def test_marker_result_ignores_poisoned_default_environment(monkeypatch):
    poison = {
        k: "POISON"
        for k in (
            "implementation_name",
            "implementation_version",
            "os_name",
            "platform_machine",
            "platform_release",
            "platform_system",
            "platform_version",
            "python_full_version",
            "platform_python_implementation",
            "python_version",
            "sys_platform",
        )
    }
    cases = ['sys_platform == "linux"', 'os_name != "nt"', 'python_version >= "3.13"', 'platform_machine == "x86_64"']
    before = [marker_applies(Marker(m)) for m in cases]
    pk_markers._cached_default_environment.cache_clear()
    monkeypatch.setattr(pk_markers, "default_environment", lambda: dict(poison))
    monkeypatch.setattr(pk_markers, "_cached_default_environment", lambda: dict(poison))
    after = [marker_applies(Marker(m)) for m in cases]
    assert before == after
    assert all(ok for ok, _ in after)
    # sanity: the poison would have changed an unmapped evaluation
    assert Marker('sys_platform == "linux"').evaluate() is False


def test_marker_env_passed_is_complete(monkeypatch):
    seen = []
    real = Marker.evaluate

    def spy(self, environment=None, *a, **k):
        seen.append(dict(environment))
        return real(self, environment, *a, **k)

    monkeypatch.setattr(Marker, "evaluate", spy)
    marker_applies(Marker('sys_platform == "linux"'))
    assert seen == [dict(MARKER_ENV, extra="")]
    assert set(seen[0]) == set(MARKER_ENV) | {"extra"}


def test_marker_tree_shape_is_pinned_to_packaging():
    import packaging

    assert packaging.__version__ == "26.3"
    m = Marker('os_name == "posix" and ("a" in platform_release or python_version >= "3")')
    assert inventory._marker_variables(m) == {"os_name", "platform_release", "python_version"}


def test_duplicate_dist_two_versions_both_survive(scan_root):
    (scan_root / "requirements.txt").write_text(
        'foo==1.0; python_version >= "3.8"\nfoo==2.0; sys_platform == "linux"\nfoo==3.0; sys_platform == "win32"\n'
    )
    inv = build_inventory(scan_root)
    assert [(e.dist, e.version, e.status) for e in inv.entries] == [
        ("foo", "1.0", Status.PINNED),
        ("foo", "2.0", Status.PINNED),
        ("foo", "3.0", Status.MARKER_SKIPPED),
    ]
