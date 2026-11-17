from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import (
    APP_CALLS_F,
    LIB_NEW,
    LIB_OLD,
    advisory,
    lib_files,
    make_scan,
    simple_lib_scan,
)


def _run(s, fetcher=None):
    return run_scan(s["app"], s["snapshot"], s["sha256"], fetcher or LocalDirFetcher(s["wheels"]))


def _scan(tmp_path, wheels, adv=None, pins=None, app=APP_CALLS_F):
    return make_scan(
        tmp_path,
        app_files={"main.py": app},
        pins=pins or {"lib": "1.0"},
        wheels=wheels,
        advisories=[adv or advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )


def test_no_fixed_version(tmp_path):
    class Rec(LocalDirFetcher):
        urls = []

        def get_json(self, url):
            self.urls.append(url)
            return super().get_json(url)

    s = _scan(tmp_path, {("lib", "1.0"): lib_files()}, advisory("GHSA-aaaa-bbbb-cccc", "lib", fixed=None))
    res = _run(s, Rec(s["wheels"]))
    f = res.findings[0]
    assert f.verdict == "unknown" and f.reason_code == "NO_FIX_VERSION" and f.evidence_level is None
    assert f.fixed_in is None
    assert not any("/2.0/" in u for u in Rec.urls)


def test_no_wheel_for_fix(tmp_path):
    s = _scan(tmp_path, {("lib", "1.0"): lib_files()})
    assert _run(s).findings[0].reason_code == "NO_WHEEL_FOR_FIX"


def test_no_wheel_for_installed(tmp_path):
    s = _scan(tmp_path, {("lib", "2.0"): lib_files(LIB_NEW)})
    res = _run(s)
    assert res.findings[0].reason_code == "NO_WHEEL"
    assert any(g.kind == "no_wheel" and g.dist == "lib" for g in res.gaps)


def test_native_only_change(tmp_path):
    s = _scan(
        tmp_path,
        {
            ("lib", "1.0"): lib_files(LIB_OLD, {"lib/_native.so": b"\x7fELF-old"}),
            ("lib", "2.0"): lib_files(LIB_OLD, {"lib/_native.so": b"\x7fELF-new"}),
        },
    )
    assert _run(s).findings[0].reason_code == "NATIVE_ONLY_CHANGE"


def test_identical_wheels_no_candidate(tmp_path):
    s = _scan(tmp_path, {("lib", "1.0"): lib_files(), ("lib", "2.0"): lib_files()})
    assert _run(s).findings[0].reason_code == "NO_CANDIDATE"


def test_only_added_symbols_no_candidate(tmp_path):
    s = _scan(tmp_path, {("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_OLD + "\n\ndef added():\n    pass\n")})
    f = _run(s).findings[0]
    assert f.reason_code == "NO_CANDIDATE" and f.targets == []


def test_target_in_module_owned_by_another_dist_is_not_queryable(tmp_path):
    # "aaa-first" sorts before "lib" and ships lib/core.py too, so the module's origin is aaa-first.
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"lib": "1.0", "aaa-first": "1.0"},
        wheels={
            ("lib", "1.0"): lib_files(LIB_OLD),
            ("lib", "2.0"): lib_files(LIB_NEW),
            ("aaa-first", "1.0"): {"lib/__init__.py": "", "lib/core.py": LIB_OLD},
        },
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    res = _run(s)
    assert res.findings[0].reason_code == "NO_CANDIDATE"
    assert any(g.kind == "collision" for g in res.gaps)


def test_first_fixed_version_above_installed_is_used(tmp_path):
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "1.5"): lib_files(LIB_NEW)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib", fixed="1.5")],
    )
    f = _run(s).findings[0]
    assert f.fixed_in == "1.5" and f.verdict == "reachable"


def test_targets_truncated_flag_on_finding(tmp_path):
    many_old = "".join(f"def f{i:02d}(): return 1\n" for i in range(25))
    many_new = "".join(f"def f{i:02d}(): return 2\n" for i in range(25))
    s = _scan(tmp_path, {("lib", "1.0"): lib_files(many_old), ("lib", "2.0"): lib_files(many_new)}, app="x = 1\n")
    f = _run(s).findings[0]
    assert f.targets_truncated is True and len(f.targets) == 20
    assert f.reason_code == "NO_APP_ENTRYPOINTS"


def test_baseline_helper_still_reachable(tmp_path):
    assert _run(simple_lib_scan(tmp_path)).findings[0].verdict == "reachable"
