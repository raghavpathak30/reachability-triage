"""Invariants over every S1 scan scenario: no negative verdict, closed registry, honest evidence."""

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import run_scan
from reachability.scan.reasons import ALLOWED_LEVELS, S1_EMITTED, ReasonCode
from reachability.scan.render import render_json, render_text
from reachability.triage.index_adapter import build_repo_index
from tests.scan.s1_support import (
    APP_CALLS_F,
    LIB_NEW,
    LIB_OLD,
    advisory,
    lib_files,
    make_scan,
    simple_lib_scan,
)
from tests.scan.test_s1_extract_rules import CASES as CRAFTED
from tests.scan.test_s1_extract_rules import make_wheel

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_SRC = REPO_ROOT / "src" / "reachability" / "scan"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

ADV = advisory("GHSA-aaaa-bbbb-cccc", "lib")
NOPATH_APP = "from lib.core import helper\n\n\ndef main():\n    return helper()\n\n\nif __name__ == '__main__':\n    main()\n"


def _std(tmp_path, **kw):
    kw.setdefault("app_files", {"main.py": APP_CALLS_F})
    kw.setdefault("pins", {"lib": "1.0"})
    kw.setdefault("wheels", {("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)})
    kw.setdefault("advisories", [ADV])
    return make_scan(tmp_path, **kw)


def s_e1(tmp_path):
    r = mat.materialize_e1(tmp_path)
    return r, LocalDirFetcher(r["wheels"]), {}


def s_simple(tmp_path):
    s = simple_lib_scan(tmp_path)
    return s, LocalDirFetcher(s["wheels"]), {}


def s_nopath(tmp_path):
    s = _std(tmp_path, app_files={"main.py": NOPATH_APP})
    return s, LocalDirFetcher(s["wheels"]), {}


def s_no_fix(tmp_path):
    s = _std(tmp_path, advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib", fixed=None)])
    return s, LocalDirFetcher(s["wheels"]), {}


def s_native_only(tmp_path):
    s = _std(
        tmp_path,
        wheels={
            ("lib", "1.0"): lib_files(LIB_OLD, {"lib/_n.so": b"a"}),
            ("lib", "2.0"): lib_files(LIB_OLD, {"lib/_n.so": b"b"}),
        },
    )
    return s, LocalDirFetcher(s["wheels"]), {}


def s_missing_fix_wheel(tmp_path):
    s = _std(tmp_path, wheels={("lib", "1.0"): lib_files(LIB_OLD)})
    return s, LocalDirFetcher(s["wheels"]), {}


def s_nested_py(tmp_path):
    bad = "x = " + "+".join(["1"] * 100000) + "\n"
    s = _std(tmp_path, wheels={("lib", "1.0"): lib_files(LIB_OLD, {"lib/d.py": bad}), ("lib", "2.0"): lib_files(LIB_NEW)})
    return s, LocalDirFetcher(s["wheels"]), {}


def s_raising_fetcher(tmp_path):
    s = _std(tmp_path)

    class Raising(LocalDirFetcher):
        def get_json(self, url):
            raise RuntimeError("boom")

    return s, Raising(s["wheels"]), {}


def s_control_chars(tmp_path):
    s = _std(tmp_path, advisories=[advisory("GHSA-a\x1b[2Jb-cccc-dddd", "lib", summary="\x1b[2J")])
    return s, LocalDirFetcher(s["wheels"]), {}


def s_collision(tmp_path):
    s = _std(
        tmp_path,
        pins={"lib": "1.0", "aaa-first": "1.0"},
        wheels={
            ("lib", "1.0"): lib_files(LIB_OLD),
            ("lib", "2.0"): lib_files(LIB_NEW),
            ("aaa-first", "1.0"): {"lib/__init__.py": "", "lib/core.py": LIB_OLD},
        },
    )
    return s, LocalDirFetcher(s["wheels"]), {}


def s_budget(tmp_path):
    s = _std(tmp_path, app_files={"main.py": NOPATH_APP}, advisories=[advisory(f"GHSA-aaaa-bbbb-{i:04d}", "lib") for i in range(4)])
    return s, LocalDirFetcher(s["wheels"]), {"max_advisories": 1}


def s_hash_mismatch(tmp_path):
    s = _std(tmp_path)
    for side in s["wheels"].glob("*.sha256"):
        side.write_text("00" * 32)
    return s, LocalDirFetcher(s["wheels"]), {}


def _crafted(name):
    def build(tmp_path):
        s = _std(tmp_path, wheels={("lib", "2.0"): lib_files(LIB_NEW)})
        whl = make_wheel(s["wheels"] / "lib-1.0-py3-none-any.whl", CRAFTED[name])
        (s["wheels"] / (whl.name + ".sha256")).write_text(hashlib.sha256(whl.read_bytes()).hexdigest())
        return s, LocalDirFetcher(s["wheels"]), {}

    return build


SCENARIOS = {
    "e1": s_e1,
    "simple": s_simple,
    "nopath": s_nopath,
    "no_fix": s_no_fix,
    "native_only": s_native_only,
    "missing_fix_wheel": s_missing_fix_wheel,
    "nested_py": s_nested_py,
    "raising_fetcher": s_raising_fetcher,
    "control_chars": s_control_chars,
    "collision": s_collision,
    "budget": s_budget,
    "hash_mismatch": s_hash_mismatch,
    **{f"crafted_{n}": _crafted(n) for n in sorted(CRAFTED)},
}


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_invariants(tmp_path, name):
    setup, fetcher, kw = SCENARIOS[name](tmp_path)
    res = run_scan(setup["app"], setup["snapshot"], setup["sha256"], fetcher, **kw)
    outputs = [render_json(res), render_text(res)]
    app_start_ids = {ep.node_id for ep in build_repo_index(Path(setup["app"])).entrypoints}
    for out in outputs:
        assert "not_reachable" not in out
    data = json.loads(outputs[0])
    assert set(data["summary"]["verdicts"]) == {"reachable", "reachable_only_from_tests", "unknown"}
    assert data["import_closure_evaluated"] is False
    for f in res.findings:
        assert f.verdict in ("reachable", "reachable_only_from_tests", "unknown")
        if f.verdict == "unknown":
            assert f.evidence_level is None
            assert f.path is None
            assert f.reason_code in {c.value for c in S1_EMITTED}
            code = ReasonCode(f.reason_code)
            assert code in ALLOWED_LEVELS
        else:
            assert f.evidence_level == "path_proven" and f.reason_code is None
            assert f.path and f.path[0].file.startswith("app/")
            assert f.path[0].caller in app_start_ids
            assert f.path[-1].callee in {t.node_id for t in f.targets}
    assert all(g.kind in {"no_wheel", "unparsed_file", "skipped_symlink", "budget", "collision", "dist_error", "excluded_dir"} for g in res.gaps)


def test_no_banned_text_in_scan_sources():
    banned = ["import socket", "import subprocess", "import requests", "import urllib", "import http", "importlib", "print("]
    for p in list((SCAN_SRC).glob("*.py")) + [REPO_ROOT / "src" / "reachability" / "cli.py"]:
        text = p.read_text()
        for b in banned:
            assert b not in text, (p, b)


def test_lowercase_negative_verdict_text_absent_from_scan_sources():
    for p in SCAN_SRC.rglob("*"):
        if p.is_file() and p.suffix == ".py":
            assert "not_reachable" not in p.read_text(), p


def test_engine_negative_verdict_member_appears_once_on_the_mapping_line():
    hits = []
    for p in sorted(SCAN_SRC.glob("*.py")):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if "NOT_REACHABLE" in line:
                hits.append((p.name, n, line.strip()))
    assert len(hits) == 1, hits
    assert hits[0][0] == "query.py" and "Verdict.NOT_REACHABLE" in hits[0][2] and "NO_PATH_FOUND" in Path(SCAN_SRC / "query.py").read_text().splitlines()[hits[0][1]]


def test_real_engine_reasons_never_take_the_unmapped_branch():
    from reachability.scan.reasons import classify_engine_reason
    from reachability.index import reachability as eng

    assert classify_engine_reason(eng.NO_ENTRYPOINTS_REASON)
    assert classify_engine_reason(eng.LOW_CONFIDENCE_REASON_PREFIX + "x")
    assert classify_engine_reason(eng.OPAQUE_CALL_REASON_PREFIX + "?:<dynamic>) at f:1 x")
    assert classify_engine_reason(eng.NAME_ESCAPE_REASON_TEMPLATE.format(target_symbol="s"))

