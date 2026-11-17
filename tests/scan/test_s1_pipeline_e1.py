import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.scan.fetch import LocalDirFetcher  # noqa: E402
from reachability.scan.pipeline import run_scan  # noqa: E402


def test_e1_end_to_end(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    res = run_scan(r["app"], r["snapshot"], r["sha256"], LocalDirFetcher(r["wheels"]))
    assert len(res.findings) == 1
    f = res.findings[0]
    assert f.advisory_id == "GHSA-r7w7-9xr2-qq2r"
    assert f.verdict == "reachable" and f.evidence_level == "path_proven" and f.reason_code is None
    assert f.fixed_in == "1.1.14" and f.version == "1.1.13"
    assert f.path[-1].callee == "langchain_openai.chat_models.base:_url_to_size"
    assert f.path[0].file == "app/main.py"
    assert "langchain_openai.chat_models.base:_url_to_size" in {t.node_id for t in f.targets}
    assert res.closure_complete is True
    assert res.stats["wheels_fetched"] == 1
