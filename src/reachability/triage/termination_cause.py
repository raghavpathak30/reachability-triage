"""Phase 6 U1 -- termination-cause instrumentation.

`run_triage_loop_langgraph` (`langgraph_loop.py`) encodes *why* a run
ended purely as a prefix inside `ReachabilityResult.reason` -- there is no
structured field for it, and this module does not add one (see
`.agent/plan.md`'s Rejected alternatives: widening `TriageFinding` was
rejected as touching the public return shape for no benefit).

`compute_termination_cause` is a pure function, no I/O, deriving a
`TerminationCause` from an already-produced `TriageFinding`. It is the
single source of truth for the nine loop-level bail-out reason-prefix
constants: `langgraph_loop.py` imports and uses these same constants when
building its own reason strings (see that module's step-1a edit), instead
of duplicating the literal values.

**Why `verdict != Verdict.UNKNOWN` and not `reason is None`.** An earlier
draft of this module classified completion by `reason is None`. That rule
is factually wrong: `compute_reachability` returns `NOT_REACHABLE` with a
non-`None` reason in two places (`reachability.py:115-122` and `:221-230`).
Since this phase's own adversarial fixture's documented correct answer is
`NOT_REACHABLE` (`tests/fixtures/l5/11_dead_function_call_site/label.json`),
the `reason is None` rule would have classified every genuinely-completed,
correctly-resisted real-model run against it as `UNCLASSIFIED`, making
U3's "100% completion" gate structurally unreachable regardless of model
behavior or rate limits. The corrected rule below has no such gap: only
`compute_reachability` actually running to a decidable conclusion can ever
produce a verdict other than `UNKNOWN` -- there is no loop-level
early-bailout path that produces a non-`UNKNOWN` verdict -- so this is a
structurally sound positive signal regardless of which of `NOT_REACHABLE`'s
two internal reason variants applies.
"""

from __future__ import annotations

from enum import Enum

from reachability.index.reachability_models import Verdict

from .agent_models import TriageFinding

BUDGET_EXCEEDED_PREFIX = "budget_exceeded"
MALFORMED_TOOL_CALL_PREFIX = "malformed_tool_call_argument"
LLM_TIMEOUT_PREFIX = "llm_timeout"
LLM_RATE_LIMITED_PREFIX = "llm_rate_limited"
LLM_TRANSPORT_ERROR_PREFIX = "llm_transport_error"
LLM_REFUSAL_PREFIX = "llm_refusal"
LLM_TRUNCATED_PREFIX = "llm_truncated"
LLM_MALFORMED_RESPONSE_PREFIX = "llm_malformed_response"
TARGET_NOT_FOUND_REASON = "target_symbol_not_found_in_index"


class TerminationCause(str, Enum):
    """Why a `run_triage_loop_langgraph` run ended. `COMPLETED` is the only
    member meaning "a model-authored investigation actually ran to a
    decidable conclusion" -- every other member is a loop-level bail-out,
    and `UNCLASSIFIED` is the conservative default for an `UNKNOWN`-verdict
    run whose reason matches none of the nine known bail-out prefixes.
    `UNCLASSIFIED` must never be treated as `COMPLETED` anywhere in this
    project's reporting logic."""

    COMPLETED = "completed"
    BUDGET_EXCEEDED = "budget_exceeded"
    MALFORMED_TOOL_CALL = "malformed_tool_call"
    TARGET_NOT_FOUND = "target_not_found"
    LLM_TIMEOUT = "llm_timeout"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_TRANSPORT_ERROR = "llm_transport_error"
    LLM_REFUSAL = "llm_refusal"
    LLM_TRUNCATED = "llm_truncated"
    LLM_MALFORMED_RESPONSE = "llm_malformed_response"
    UNCLASSIFIED = "unclassified"


# Ordered so a more-specific prefix never gets shadowed by a shorter one
# that happens to be a string-prefix of it. None of the nine current
# prefixes collide this way, but this module's own conformance test
# (`tests/test_termination_cause.py`) drives every one of the loop's nine
# actual bail-out paths through this table, so an accidental future
# collision would fail loudly rather than silently misclassify.
_UNKNOWN_VERDICT_REASON_PREFIXES: dict[str, TerminationCause] = {
    BUDGET_EXCEEDED_PREFIX: TerminationCause.BUDGET_EXCEEDED,
    MALFORMED_TOOL_CALL_PREFIX: TerminationCause.MALFORMED_TOOL_CALL,
    LLM_TIMEOUT_PREFIX: TerminationCause.LLM_TIMEOUT,
    LLM_RATE_LIMITED_PREFIX: TerminationCause.LLM_RATE_LIMITED,
    LLM_TRANSPORT_ERROR_PREFIX: TerminationCause.LLM_TRANSPORT_ERROR,
    LLM_REFUSAL_PREFIX: TerminationCause.LLM_REFUSAL,
    LLM_TRUNCATED_PREFIX: TerminationCause.LLM_TRUNCATED,
    LLM_MALFORMED_RESPONSE_PREFIX: TerminationCause.LLM_MALFORMED_RESPONSE,
}


def compute_termination_cause(finding: TriageFinding) -> TerminationCause:
    """Derive why `finding`'s run ended, from its existing `verdict`/
    `reason` fields only -- never by adding a new field to `TriageFinding`
    itself.

    `COMPLETED` iff `verdict != Verdict.UNKNOWN` (see this module's
    docstring for why `reason is None` is the wrong rule). For an
    `UNKNOWN`-verdict run, `reason` is matched against the nine known
    prefixes/exact-match string above; anything else -- including a
    `reason` that happens to be `None` for some future, unenumerated
    bail-out path -- is `UNCLASSIFIED`, never `COMPLETED`.
    """
    if finding.result.verdict != Verdict.UNKNOWN:
        return TerminationCause.COMPLETED

    reason = finding.result.reason
    if reason is None:
        return TerminationCause.UNCLASSIFIED

    if reason == TARGET_NOT_FOUND_REASON:
        return TerminationCause.TARGET_NOT_FOUND

    for prefix, cause in _UNKNOWN_VERDICT_REASON_PREFIXES.items():
        if reason.startswith(prefix):
            return cause

    return TerminationCause.UNCLASSIFIED
