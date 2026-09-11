"""Gate (ii) — L5-reproduction sanity check for `run_triage_loop`.

For a representative subset of `tests/fixtures/l5/*/label.json` covering
all four `Verdict` values, this asserts that running the full agent loop
(with `DeterministicPolicyStubLLMClient`'s backward-BFS policy) against a
`build_repo_index`-built `RepoIndex` reproduces the verdict/reason a
direct `compute_reachability` call produces, and that every `CallEdge` in
the loop's emitted `path` was actually returned by a real `find_callers`
call dispatched during that same run (via the `_on_raw_tool_result` test
hook — see `agent_loop.py`'s docstring for why this must observe the
loop's actual dispatch, not a test-side recomputation).

`label.json`'s `sink` field is `{file, line}`, not `module`/`symbol` —
`scripts/measure_l5.py`'s `_module_dotted_name`/`resolve_target` helpers
convert that into the `(target_module, target_symbol)` pair this test
needs; reused directly here rather than re-derived.

**`_on_raw_tool_result` is test-only instrumentation, not a general
extension point.** This file is the reason it exists: it lets this test
observe the real, structured `list[CallEdge]` that `run_triage_loop`'s
own `find_callers` dispatches actually returned during the run, rather
than a test-side recomputation that would prove nothing about the loop's
real behavior. Do not wire it into U5's FastAPI job lifecycle or any
other production caller.
"""

import sys
from pathlib import Path

import pytest

from reachability.index.reachability import compute_reachability
from reachability.index.reachability_models import Verdict
from reachability.triage.agent_loop import run_triage_loop
from reachability.triage.index_adapter import build_repo_index
from reachability.triage.stub_llm import DeterministicPolicyStubLLMClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import measure_l5  # noqa: E402

FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures" / "l5"

# Chosen to cover all four Verdict values, per step 9: 02 is a multi-hop
# `reachable` case (exercises the backward-BFS policy's fixed-point walk
# across 3 edges, not just one hop); 07 is `reachable_only_from_tests`;
# 09 is `not_reachable`; 17 is `unknown` with a None path (Step 5's
# name-referenced-outside-call-position degradation).
#
# Deliberately excludes the dynamic-dispatch/bridging `unknown` fixtures
# (13, 14, 14b, 15, 16, 16b, 18) even though they are also `unknown`:
# `compute_reachability`'s own bridging rule (`reachability.py`'s
# `_match_target` `"?:"`-prefixed branch) matches those paths against a
# synthetic id like `"?:vulnerable"` or `"?:<dynamic>"`, never against the
# literal node id `search_symbol` confirms (e.g. `"pkg.sink:vulnerable"`).
# `find_callers` filters by exact `callee_id` string, so a backward BFS
# seeded from the confirmed literal node id can never discover edges whose
# `callee_id` is one of those synthetic bridging ids -- there is no tool
# call a policy client could make (short of hard-coding knowledge of the
# reachability engine's internal bridging ids, which would defeat the
# point of gate (ii)) that would surface them. This is not a client bug;
# it is why this subset only exercises the path-provenance check on
# fixtures whose path is built entirely from literal, by-name call edges
# (02, 07), while still covering the `unknown` verdict via a fixture (17)
# whose path is `None` -- consistent with the plan's success criteria,
# which require the check to hold "for every fixture whose label.json
# allows a non-None path," not for every fixture in the full L5 corpus.
SELECTED_FIXTURES = [
    "02_transitive_three_hop",
    "07_test_only_call",
    "09_never_imported",
    "17_framework_callback_reference",
]

BUDGET = 50


def _resolve_target(fixture_dir: Path, symbol_index: dict) -> tuple[str, str]:
    label = measure_l5.load_label(fixture_dir)
    target_module = measure_l5._module_dotted_name(label["sink"]["file"])
    target_symbol = measure_l5.resolve_target(
        symbol_index, target_module, label["sink"]["line"], fixture_dir.name
    )
    return target_module, target_symbol


@pytest.mark.parametrize("fixture_name", SELECTED_FIXTURES)
def test_loop_reproduces_direct_compute_reachability(fixture_name):
    fixture_dir = FIXTURES_ROOT / fixture_name
    repo_root = fixture_dir / "repo"

    repo_index = build_repo_index(repo_root)
    target_module, target_symbol = _resolve_target(fixture_dir, repo_index.symbol_index)

    reference = compute_reachability(
        target_module, target_symbol, repo_index.entrypoints, repo_index.edges, repo_index.report
    )

    raw_find_callers_edges: list = []

    def _capture(tool_name, arguments, raw_result):
        if tool_name == "find_callers":
            raw_find_callers_edges.extend(raw_result)

    client = DeterministicPolicyStubLLMClient(target_module, target_symbol)
    finding = run_triage_loop(
        client,
        repo_index,
        target_module,
        target_symbol,
        budget=BUDGET,
        _on_raw_tool_result=_capture,
    )

    assert finding.result.verdict == reference.verdict
    assert finding.result.reason == reference.reason

    if finding.result.path is not None:
        assert reference.path is not None
        for edge in finding.result.path:
            assert edge in raw_find_callers_edges
