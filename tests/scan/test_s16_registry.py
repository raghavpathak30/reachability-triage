import json
from pathlib import Path

PREREG = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan" / "eval" / "prereg"


def test_s16_expected_json_shape():
    d = json.loads((PREREG / "s1_6_expected.json").read_text())
    proven = d["expected_path_proven"]
    assert {e["advisory_id"] for e in proven} == {"GHSA-3w57-8xmc-8v26", "GHSA-w6j9-cwv2-h6wq"}
    for e in proven:
        assert e["true_symbols"] and e["fix_commits"]
    allowed = d["unknown_or_path_proven_to_true_symbol"]
    assert {e["advisory_id"] for e in allowed} == {"GHSA-9j54-fg26-wv3r", "GHSA-pxh4-856f-4h89", "GHSA-jhr6-gm9c-rqjv"}
    for e in allowed:
        assert e["true_symbols"] and e["fix_commits"]
    unknown = d["expected_unknown"]
    assert {e["advisory_id"] for e in unknown} == {
        "GHSA-5849-834g-hvw5", "GHSA-4vqv-7265-gm2w", "GHSA-x3rh-gjgm-fmqc", "GHSA-cg8m-r9f2-5wm2",
    }
    assert "sha256" in d["snapshot"]
    assert any("stop, no merge" in r for r in d["pivot_rule"])
    assert any("unverified" in r for r in d["pivot_rule"])
    assert set(d["eval_rows"]) >= {"E1b"}
