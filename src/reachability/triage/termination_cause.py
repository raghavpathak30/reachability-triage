"""Phase 6 U1 -- termination-cause instrumentation.

`run_triage_loop_langgraph` (`langgraph_loop.py`) encodes *why* a run
ended purely as a prefix inside `ReachabilityResult.reason` -- there is no
structured field for it, and this module does not add one (see
`.agent/plan.md`'s Rejected alternatives: widening `TriageFinding` was
rejected as touching the public return shape for no benefit).

`compute_termination_cause` is a pure function, no I/O, deriving a
`TerminationCause` from an already-produced `TriageFinding`. It is the
single source of truth for the ten loop-level bail-out reason constants
(nine prefixes plus one exact-match reason): `langgraph_loop.py` imports
and uses these same constants when building its own reason strings (see
that module's step-1a edit), instead of duplicating the literal values.

**Why `verdict != Verdict.UNKNOWN` and not `reason is None`.** An earlier
draft of this module classified completion by `reason is None`. That rule
is factually wrong: `compute_reachability` returns `NOT_REACHABLE` with a
non-`None` reason (`reachability.py:226-235`, its final step, the only
`NOT_REACHABLE` return). Its four `UNKNOWN` returns each classify to their own
cause here: zero entrypoints as `NO_ENTRYPOINTS` via `NO_ENTRYPOINTS_REASON`,
and the low-confidence, opaque-call and name-escape returns as
`LOW_CONFIDENCE_PATH`, `OPAQUE_CALL` and `NAME_ESCAPE` via the reason
constants in `reachability.py`; none of them falls to `UNCLASSIFIED`.
Since this phase's own adversarial fixture's documented correct answer is
`NOT_REACHABLE` (`tests/fixtures/l5/11_dead_function_call_site/label.json`),
the `reason is None` rule would have classified every genuinely-completed,
correctly-resisted real-model run against it as `UNCLASSIFIED`, making
U3's "100% completion" gate structurally unreachable regardless of model
behavior or rate limits. The corrected rule below has no such gap: only
`compute_reachability` actually running to a decidable conclusion can ever
produce a verdict other than `UNKNOWN` -- there is no loop-level
early-bailout path that produces a non-`UNKNOWN` verdict -- so this is a
structurally sound positive signal regardless of the `NOT_REACHABLE` reason text.
"""

from __future__ import annotations

import re
from enum import Enum

from reachability.index.reachability import (
    LOW_CONFIDENCE_REASON_PREFIX,
    NAME_ESCAPE_REASON_TEMPLATE,
    NO_ENTRYPOINTS_REASON,
    OPAQUE_CALL_REASON_PREFIX,
)
from reachability.index.reachability_models import Verdict

from .agent_models import TriageFinding

BUDGET_EXCEEDED_PREFIX = "budget_exceeded"
MALFORMED_TOOL_CALL_PREFIX = "malformed_tool_call_argument"
LLM_TIMEOUT_PREFIX = "llm_timeout"
LLM_RATE_LIMITED_PREFIX = "llm_rate_limited"
LLM_REQUEST_TOO_LARGE_PREFIX = "llm_request_too_large"
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
    run whose reason matches none of the nine loop prefixes, the
    exact-match reasons or the engine reason constants.
    `UNCLASSIFIED` must never be treated as `COMPLETED` anywhere in this
    project's reporting logic. `NO_ENTRYPOINTS`: analysis ran, answer
    undetermined; NOT the loop bailing out; NOT COMPLETED.
    `LOW_CONFIDENCE_PATH`: analysis ran, answer undetermined; NOT a loop
    bail-out; NOT COMPLETED. `OPAQUE_CALL`: analysis ran, answer
    undetermined; NOT a loop bail-out; NOT COMPLETED. `NAME_ESCAPE`:
    analysis ran, answer undetermined; NOT a loop bail-out; NOT COMPLETED."""

    COMPLETED = "completed"
    BUDGET_EXCEEDED = "budget_exceeded"
    MALFORMED_TOOL_CALL = "malformed_tool_call"
    TARGET_NOT_FOUND = "target_not_found"
    LLM_TIMEOUT = "llm_timeout"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_REQUEST_TOO_LARGE = "llm_request_too_large"
    LLM_TRANSPORT_ERROR = "llm_transport_error"
    LLM_REFUSAL = "llm_refusal"
    LLM_TRUNCATED = "llm_truncated"
    LLM_MALFORMED_RESPONSE = "llm_malformed_response"
    NO_ENTRYPOINTS = "no_entrypoints"
    LOW_CONFIDENCE_PATH = "low_confidence_path"
    OPAQUE_CALL = "opaque_call"
    NAME_ESCAPE = "name_escape"
    UNCLASSIFIED = "unclassified"


_ENGINE_PREFIX_CAUSES: tuple[tuple[str, TerminationCause], ...] = (
    (LOW_CONFIDENCE_REASON_PREFIX, TerminationCause.LOW_CONFIDENCE_PATH),
    (OPAQUE_CALL_REASON_PREFIX, TerminationCause.OPAQUE_CALL),
)

_NAME_ESCAPE_PARTS = NAME_ESCAPE_REASON_TEMPLATE.split("{target_symbol}")
if len(_NAME_ESCAPE_PARTS) != 2:
    raise RuntimeError("NAME_ESCAPE_REASON_TEMPLATE must contain exactly one {target_symbol} placeholder")
_NAME_ESCAPE_RE = re.compile("(.+)".join(re.escape(part) for part in _NAME_ESCAPE_PARTS), re.DOTALL)

# Ordered so a more-specific prefix never gets shadowed by a shorter one
# that happens to be a string-prefix of it. None of the nine current
# prefixes collide this way. This table holds the nine prefixes; the tenth
# bail-out path (target not found) is an exact-match reason outside it. This
# module's own conformance test (`tests/test_termination_cause.py`) drives
# every one of the ten bail-out paths through `compute_termination_cause`,
# so an accidental future collision would fail loudly rather than silently
# misclassify.
_UNKNOWN_VERDICT_REASON_PREFIXES: dict[str, TerminationCause] = {
    BUDGET_EXCEEDED_PREFIX: TerminationCause.BUDGET_EXCEEDED,
    MALFORMED_TOOL_CALL_PREFIX: TerminationCause.MALFORMED_TOOL_CALL,
    LLM_TIMEOUT_PREFIX: TerminationCause.LLM_TIMEOUT,
    LLM_RATE_LIMITED_PREFIX: TerminationCause.LLM_RATE_LIMITED,
    LLM_REQUEST_TOO_LARGE_PREFIX: TerminationCause.LLM_REQUEST_TOO_LARGE,
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
    `UNKNOWN`-verdict run, `reason` is matched against the exact-match
    reasons, the engine reason constants (`startswith` for the low-confidence
    and opaque-call prefixes, a full-match regex for the name-escape
    template) and then the nine loop prefixes above; anything else -- including a
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

    if reason == NO_ENTRYPOINTS_REASON:
        return TerminationCause.NO_ENTRYPOINTS

    for engine_prefix, engine_cause in _ENGINE_PREFIX_CAUSES:
        if reason.startswith(engine_prefix):
            return engine_cause

    if _NAME_ESCAPE_RE.fullmatch(reason):
        return TerminationCause.NAME_ESCAPE

    for prefix, cause in _UNKNOWN_VERDICT_REASON_PREFIXES.items():
        if reason.startswith(prefix):
            return cause

    return TerminationCause.UNCLASSIFIED
