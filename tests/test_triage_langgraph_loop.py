"""Phase LangGraph, Unit 2's own gate test: `run_triage_loop_langgraph` runs
at least one fixture end-to-end and produces a well-formed `TriageFinding`.

Calls `run_triage_loop_langgraph` directly, not through `job_runner.py`, so
`TRIAGE_LOOP_BACKEND` is not in play here -- the flag's job-runner wiring is
exercised separately, in `tests/test_triage_job_runner.py`.

No verdict-correctness assertion here -- that is Unit 5's gate, not this
one's; see `agent_docs/PHASE_LANGGRAPH.md`'s Unit 2 gate wording.
"""

import sys
from pathlib import Path

from reachability.index.reachability_models import ReachabilityResult, Verdict
from reachability.triage.agent_models import TriageFinding
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.langgraph_loop import run_triage_loop_langgraph
from reachability.triage.stub_llm import DeterministicPolicyStubLLMClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import measure_l5  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "l5" / "09_never_imported"

BUDGET = 50


def test_langgraph_loop_runs_fixture_end_to_end():
    repo_root = FIXTURE_DIR / "repo"
    repo_index = build_repo_index(repo_root)

    label = measure_l5.load_label(FIXTURE_DIR)
    target_module = measure_l5._module_dotted_name(label["sink"]["file"])
    target_symbol = measure_l5.resolve_target(
        repo_index.symbol_index, target_module, label["sink"]["line"], FIXTURE_DIR.name
    )

    client = DeterministicPolicyStubLLMClient(target_module, target_symbol)

    finding = run_triage_loop_langgraph(
        client,
        repo_index,
        target_module,
        target_symbol,
        budget=BUDGET,
    )

    assert isinstance(finding, TriageFinding)
    assert isinstance(finding.result, ReachabilityResult)
    assert isinstance(finding.result.verdict, Verdict)
    assert isinstance(finding.tool_calls, list)
