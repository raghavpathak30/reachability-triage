import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan"
REPO = Path(__file__).resolve().parents[2]


def test_prereg_json_equals_each_fixture_expected_json():
    prereg = json.loads((FIXTURES / "eval" / "prereg" / "a1b_expected.json").read_text())
    assert set(prereg) == {"uv_ws_one", "uv_ws_two", "uv_ws_escape", "req_huge_digit"}
    for name, exp in prereg.items():
        assert json.loads((FIXTURES / "lockfiles" / name / "expected.json").read_text()) == exp
