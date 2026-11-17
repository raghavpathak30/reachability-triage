import json
from pathlib import Path

PREREG = Path(__file__).resolve().parents[1] / "fixtures" / "app_scan" / "eval" / "prereg"
PATH_PROVEN = {"GHSA-w6j9-cwv2-h6wq", "GHSA-5849-834g-hvw5", "GHSA-4vqv-7265-gm2w", "GHSA-x3rh-gjgm-fmqc", "GHSA-3w57-8xmc-8v26"}


def test_e1_expected():
    d = json.loads((PREREG / "s1_e1_expected.json").read_text())
    assert d["expected"]["final_callee"] == "langchain_openai.chat_models.base:_url_to_size"
    assert d["expected"]["verdict"] == "reachable"


def test_openwebui_expected():
    d = json.loads((PREREG / "s1_openwebui_expected.json").read_text())
    assert {e["advisory_id"] for e in d["expected_path_proven"]} == PATH_PROVEN
    unk = {e["advisory_id"] for e in d["expected_unknown"]}
    assert len(unk) == 11 and not unk & PATH_PROVEN


def test_realapp_plan():
    text = (PREREG / "s1_realapp_plan.md").read_text()
    assert "c7ea4e15b83912960fc5cbd42c6003dd630e422ec4dfbfc4ed19445573e1b08c" in text
    for sha in ("49943b5", "aabb901", "b0e6098e940942281d9c83f3529ea117287a3a22"):
        assert sha in text
