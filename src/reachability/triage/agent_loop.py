"""The U3 triage agent loop.

`run_triage_loop` is the only caller of the three frozen L4 tools
(`search_symbol`/`find_callers`/`resolve_import`, `query.py:8,17,21`) and
of `compute_reachability` (`reachability.py:108-230`); it never modifies
`query.py` -- L1-L4 (`src/reachability/index/`) is a frozen surface per
CLAUDE.md. The loop's own output is never a bare verdict: once an
`AgentAction` names a `(target_module, target_symbol)` pair, this module
calls `compute_reachability` and wraps the resulting `ReachabilityResult`
into a `TriageFinding` -- the LLM client's own text is never the verdict
source (`agent_models.py`'s `TriageFinding` docstring).

Every tool-call result passes through exactly one call site,
`_append_tool_result`, which is also the only place in this module that
constructs a `Message` from tool output, and always with `role="tool"` --
never `"system"`/`"developer"` -- so a comment or docstring inside a
scanned repo can never re-prioritize the agent's goal by posing as a
higher-privilege message
(`agent_docs/PHASE2_TRIAGE_AGENT.md:148-150`). That same call site is
also where `sandbox_untrusted_text` (`sandbox.py`) is invoked, from this
module's very first commit -- as an identity stub, later a real
detector -- so this file's own code never changes when U4 fills in that
stub's body.

Budget enforcement is a plain per-run integer counter of tool calls
actually dispatched, checked *before* asking the LLM client for its next
action at all -- not a token-based or cost-accounting mechanism (that
stays deferred; `agent_docs/PHASE2_TRIAGE_AGENT.md` §5). Three
degradation cases must each produce a `TriageFinding` with verdict
`unknown`, never an exception or a hang:

- (a) budget exceeded before a final answer was reached;
- (b) a `FinalAnswerAction` names a `(target_module, target_symbol)` pair
  whose fully-qualified id was never confirmed present by any
  `search_symbol` call made during this run -- the harness-level
  substitute for `query.py` itself signaling "doesn't exist," since that
  frozen surface returns an empty list rather than raising or returning
  `None` on a no-match `search_symbol`/`find_callers` call;
- (c) a `ToolCallAction` whose arguments don't satisfy `TOOL_SCHEMAS`
  (missing key, or present but wrong type) -- checked, and rejected,
  *before* any tool is dispatched, so a malformed call never reaches
  `sandbox_untrusted_text` in either the stub or the real implementation.

`AgentLoopError` is reserved for genuine internal-consistency bugs in
this module's own dispatch table (a tool name that passed
`TOOL_SCHEMAS` validation but has no matching branch in `_dispatch_tool`)
-- it is never raised for cases (a)/(b)/(c), which always degrade to a
returned `TriageFinding` instead.
"""

from __future__ import annotations

from collections.abc import Callable

from reachability.index import (
    Verdict,
    compute_reachability,
    find_callers,
    resolve_import,
    search_symbol,
)
from reachability.index.reachability_models import ReachabilityResult

from .agent_models import Message, ToolCallRecord, TriageFinding
from .index_adapter import RepoIndex
from .sandbox import sandbox_untrusted_text
from .stub_llm import FinalAnswerAction, StubLLMClient, ToolCallAction

TOOL_SCHEMAS: dict[str, tuple[tuple[str, type], ...]] = {
    "search_symbol": (("pattern", str),),
    "find_callers": (("node_id", str),),
    "resolve_import": (("module", str), ("name", str)),
}


class AgentLoopError(RuntimeError):
    """Raised only for a genuine programming error in this module's own
    dispatch table -- never for a gate-(i) degradation case."""


def _is_well_formed_tool_call(tool_name: str, arguments: dict[str, object]) -> bool:
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        return False
    for key, expected_type in schema:
        if key not in arguments or not isinstance(arguments[key], expected_type):
            return False
    return True


def _dispatch_tool(tool_name: str, arguments: dict[str, object], repo_index: RepoIndex) -> object:
    if tool_name == "search_symbol":
        return search_symbol(repo_index.symbol_index, arguments["pattern"])
    if tool_name == "find_callers":
        return find_callers(repo_index.edges, arguments["node_id"])
    if tool_name == "resolve_import":
        return resolve_import(repo_index.report, arguments["module"], arguments["name"])
    raise AgentLoopError(f"unregistered tool in dispatch table: {tool_name!r}")


def _confirmed_id_matches_target(node_id: str, target_module: str, target_symbol: str | None) -> bool:
    """Harness-level check for case (b): does a `search_symbol`-confirmed
    `node_id` correspond to `(target_module, target_symbol)`?

    Deliberately independent of `reachability.py`'s own (private,
    unexported) target-matching logic -- this is harness bookkeeping, not
    a reachability computation, and does not need `?:`/`ext:`-prefixed id
    handling since `search_symbol` only ever returns first-party
    `SymbolNode`s from `repo_index.symbol_index`.
    """
    if ":" in node_id:
        module_part, qualname = node_id.split(":", 1)
    else:
        module_part, qualname = node_id, ""

    if module_part != target_module:
        return False
    if target_symbol is None:
        return not qualname
    if not qualname:
        return False
    final_segment = qualname.rsplit(".", 1)[-1].split("@")[0]
    return final_segment == target_symbol


def _append_tool_result(context: list[Message], tool_name: str, sanitized: str) -> None:
    """The single call site that appends a tool result to `context`.

    Always `role="tool"` -- no other function in this module constructs a
    `Message` from tool output, and no function ever sets
    `role="system"`/`"developer"` for tool output.
    """
    context.append(Message(role="tool", content=sanitized))


def run_triage_loop(
    llm_client: StubLLMClient,
    repo_index: RepoIndex,
    target_module: str,
    target_symbol: str | None,
    budget: int,
    _on_raw_tool_result: Callable[[str, dict[str, object], object], None] | None = None,
) -> TriageFinding:
    """Run the triage agent loop for one job and return its `TriageFinding`.

    `target_module`/`target_symbol` seed the loop's initial context (the
    job's configured target); the verdict itself always comes from
    calling `compute_reachability` with whatever `(target_module,
    target_symbol)` the `FinalAnswerAction` that ends the run actually
    names, not from these parameters -- see `stub_llm.py`'s
    `FinalAnswerAction` docstring for why that distinction matters.

    `_on_raw_tool_result` is **test-only instrumentation, not a general
    extension point.** When not `None`, it is invoked, for every
    dispatched tool call, immediately after the real tool function
    returns and before its result is stringified or sanitized, with
    `(tool_name, arguments, raw_result_object)` -- the actual structured
    return value (e.g. the real `list[CallEdge]`), not a re-derived proxy.
    It exists solely so `tests/test_triage_agent_loop_l5.py` can verify
    gate (ii)'s path-provenance property against what this loop's real
    dispatch calls actually returned. It fires unconditionally alongside,
    never instead of, the normal sanitize-and-append path below. Do not
    wire this into U5's FastAPI job lifecycle or any other production
    caller -- it is not a callback/observer mechanism.
    """
    context: list[Message] = [
        Message(role="user", content=f"target_module={target_module!r} target_symbol={target_symbol!r}"),
    ]
    tool_calls: list[ToolCallRecord] = []
    confirmed_symbol_ids: set[str] = set()
    calls_made = 0

    while True:
        if calls_made >= budget:
            return TriageFinding(
                result=ReachabilityResult(
                    target_module=target_module,
                    target_symbol=target_symbol,
                    verdict=Verdict.UNKNOWN,
                    path=None,
                    reason="budget_exceeded: tool-call budget exhausted before a final answer was reached",
                ),
                rationale="",
                tool_calls=tool_calls,
            )

        action = llm_client.next_action(context)

        if isinstance(action, ToolCallAction):
            if not _is_well_formed_tool_call(action.tool_name, action.arguments):
                return TriageFinding(
                    result=ReachabilityResult(
                        target_module=target_module,
                        target_symbol=target_symbol,
                        verdict=Verdict.UNKNOWN,
                        path=None,
                        reason=f"malformed_tool_call_argument: {action.tool_name}",
                    ),
                    rationale="",
                    tool_calls=tool_calls,
                )

            raw_result = _dispatch_tool(action.tool_name, action.arguments, repo_index)
            calls_made += 1

            if _on_raw_tool_result is not None:
                _on_raw_tool_result(action.tool_name, action.arguments, raw_result)

            if action.tool_name == "search_symbol":
                confirmed_symbol_ids.update(node.node_id for node in raw_result)

            sanitized = sandbox_untrusted_text(str(raw_result))
            _append_tool_result(context, action.tool_name, sanitized)
            tool_calls.append(
                ToolCallRecord(
                    sequence=len(tool_calls),
                    tool_name=action.tool_name,
                    arguments=action.arguments,
                    result=sanitized,
                )
            )
            continue

        assert isinstance(action, FinalAnswerAction)

        if not any(
            _confirmed_id_matches_target(node_id, action.target_module, action.target_symbol)
            for node_id in confirmed_symbol_ids
        ):
            return TriageFinding(
                result=ReachabilityResult(
                    target_module=action.target_module,
                    target_symbol=action.target_symbol,
                    verdict=Verdict.UNKNOWN,
                    path=None,
                    reason="target_symbol_not_found_in_index",
                ),
                rationale=action.rationale,
                tool_calls=tool_calls,
            )

        result = compute_reachability(
            action.target_module,
            action.target_symbol,
            repo_index.entrypoints,
            repo_index.edges,
            repo_index.report,
        )
        return TriageFinding(result=result, rationale=action.rationale, tool_calls=tool_calls)
