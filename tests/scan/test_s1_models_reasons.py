import json

from reachability.index.reachability import (
    LOW_CONFIDENCE_REASON_PREFIX,
    NAME_ESCAPE_REASON_TEMPLATE,
    NO_ENTRYPOINTS_REASON,
    OPAQUE_CALL_REASON_PREFIX,
)
from reachability.scan.models import Finding, Gap, PathEdge, Target
from reachability.scan.reasons import (
    ALLOWED_LEVELS,
    S1_EMITTED,
    EvidenceLevel,
    ReasonCode,
    aggregate_reasons,
    classify_engine_reason,
)
from reachability.scan.textsafe import strip_control


def test_every_code_has_allowed_levels():
    assert set(ALLOWED_LEVELS) == set(ReasonCode)
    assert all(levels for levels in ALLOWED_LEVELS.values())
    assert ALLOWED_LEVELS[ReasonCode.NO_FIX_VERSION] == {EvidenceLevel.NO_TARGET_SYMBOL}


def test_s1_does_not_emit_closure_codes():
    assert ReasonCode.NOT_IMPORTED_BY_APP_CODE not in S1_EMITTED
    assert ReasonCode.DEPENDENCY_PIN_FIX not in S1_EMITTED


def test_engine_reasons_map_to_one_code_each():
    assert classify_engine_reason(NO_ENTRYPOINTS_REASON) is ReasonCode.NO_APP_ENTRYPOINTS
    assert classify_engine_reason(LOW_CONFIDENCE_REASON_PREFIX + "self_unresolved") is ReasonCode.LOW_CONFIDENCE_PATH
    assert classify_engine_reason(OPAQUE_CALL_REASON_PREFIX + "?:<dynamic>) at x.py:3 ...") is ReasonCode.OPAQUE_CALL
    assert classify_engine_reason(NAME_ESCAPE_REASON_TEMPLATE.format(target_symbol="f")) is ReasonCode.NAME_ESCAPE
    assert classify_engine_reason("something else") is None
    assert classify_engine_reason(None) is None


def test_reason_priority():
    assert aggregate_reasons([ReasonCode.NO_PATH_FOUND, ReasonCode.NAME_ESCAPE]) is ReasonCode.NAME_ESCAPE
    assert aggregate_reasons([ReasonCode.NAME_ESCAPE, ReasonCode.OPAQUE_CALL]) is ReasonCode.OPAQUE_CALL
    assert (
        aggregate_reasons([ReasonCode.OPAQUE_CALL, ReasonCode.LOW_CONFIDENCE_PATH, ReasonCode.NO_PATH_FOUND])
        is ReasonCode.LOW_CONFIDENCE_PATH
    )
    assert aggregate_reasons([]) is ReasonCode.NO_PATH_FOUND


def test_strip_control():
    out = strip_control("a\x1b[2Jb\x85\x9bc\x7fd")
    assert all(ord(c) >= 0x20 and not 0x7F <= ord(c) <= 0x9F for c in out.replace("\n", ""))
    assert "\x1b" not in out
    assert out.startswith("a") and out.endswith("d")


def test_finding_to_dict_is_json_serialisable():
    t = Target("m:f", "m", "f", 0, "dist/m.py", 3, "changed")
    f = Finding(
        "GHSA-x", ["CVE-1"], "d", "1.0", "1.1", "reachable", "path_proven", None,
        path=[PathEdge("app/main.py", 2, "main", "m:f")], targets=[t],
    )
    d = json.loads(json.dumps(f.to_dict()))
    assert d["path"][0]["callee"] == "m:f"
    assert d["targets"][0]["confidence"] == "medium" and d["targets"][0]["named"] is False
    assert Gap("budget", "x", "cut").to_dict()["kind"] == "budget"
