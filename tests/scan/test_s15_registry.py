import json
from pathlib import Path

from reachability.scan.advisories import Record, _reduce
from reachability.scan.reasons import ALLOWED_LEVELS, EvidenceLevel, ReasonCode

PREREG = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan" / "eval" / "prereg"


def test_new_code_in_enum_and_allowed_only_at_module_imported():
    code = ReasonCode("PATH_TO_UNCONFIRMED_TARGET")
    assert ALLOWED_LEVELS[code] == frozenset({EvidenceLevel.MODULE_IMPORTED_BY_APP})


def test_s15_expected_json_shape():
    d = json.loads((PREREG / "s1_5_expected.json").read_text())
    s1 = json.loads((PREREG / "s1_openwebui_expected.json").read_text())
    proven = d["expected_path_proven"]
    assert len(proven) == 2
    for e in proven:
        assert e["true_symbols"] and isinstance(e["true_symbols"], list)
        assert e["final_callee"] in e["true_symbols"]
    unknown = d["expected_unknown"]
    assert len(unknown) == 4
    for e in unknown:
        assert isinstance(e["true_symbols"], list)
    reported = d["reported_no_expectation"]
    assert len(reported) == 11
    assert {e["advisory_id"] for e in reported} == {e["advisory_id"] for e in s1["expected_unknown"]}
    assert "sha256" in d["snapshot"]


def test_record_carries_summary_and_details():
    raw = {"id": "X-1", "summary": "sum", "details": "det `f()`"}
    rec = _reduce(raw)
    assert rec.summary == "sum"
    assert rec.details == "det `f()`"
    assert Record("a", (), ()).details == ""


def test_non_string_details_coerced_not_fatal():
    rec = _reduce({"id": "X-2", "summary": 5, "details": ["a"]})
    assert rec.summary == "" and rec.details == ""
    big = _reduce({"id": "X-3", "details": "a" * (70 * 1024)})
    assert len(big.details) == 64 * 1024
