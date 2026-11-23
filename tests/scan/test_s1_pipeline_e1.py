import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.scan.fetch import LocalDirFetcher  # noqa: E402
from reachability.scan.pipeline import run_scan  # noqa: E402


E1_LABEL = REPO_ROOT / "tests" / "fixtures" / "app_scan" / "eval" / "E1" / "label.json"


def test_e1_end_to_end(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    res = run_scan(r["app"], r["snapshot"], r["sha256"], LocalDirFetcher(r["wheels"]))
    assert len(res.findings) == 1
    f = res.findings[0]
    assert f.advisory_id == "GHSA-r7w7-9xr2-qq2r"
    label = json.loads((E1_LABEL).read_text())
    assert f.verdict == label["verdict"] == "unknown" and f.evidence_level is None
    assert f.reason_code == label["reason_code"] == "PATH_TO_UNCONFIRMED_TARGET"
    assert f.fixed_in == "1.1.14" and f.version == "1.1.13"
    assert f.path is None and f.partial_path
    assert f.partial_path[-1].callee in label["partial_path_may_end_at"]
    assert f.partial_path[0].file == "app/main.py"
    assert all(t.confidence == "medium" and not t.in_fix_commit for t in f.targets)
    assert "langchain_openai.chat_models.base:_url_to_size" in {t.node_id for t in f.targets}
    assert res.closure_complete is True
    assert res.stats["wheels_fetched"] == 1
