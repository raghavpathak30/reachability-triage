"""Phase LangGraph, Unit 6 — as of this unit, this module is the sole
triage-loop implementation: the old hand-rolled loop module is deleted,
not deprecated, and the backend-selection environment flag that used to
gate this path is gone too -- there is nothing left to select between
(`agent_docs/PHASE_LANGGRAPH.md` §3).

This module reproduces the deleted hand-rolled `run_triage_loop`'s
exact control flow -- four termination shapes, budget check before the
model call, the sandbox call between tool dispatch and re-entry into the
agent node -- as a raw `StateGraph` (not `create_agent`/`create_react_agent`,
per the phase doc's design constraints). It imports, never duplicates,
`TOOL_SCHEMAS`/`_is_well_formed_tool_call`/`_dispatch_tool`/
`_confirmed_id_matches_target`/`AgentLoopError` from `.tool_dispatch` and
`sandbox_untrusted_text` from `.sandbox`.

The budget is a hand-rolled counter carried in graph state, checked via a
conditional edge -- never `recursion_limit`/`GraphRecursionError` (the
phase doc's explicit prohibition; see `.agent/plan.md`'s escalation check
for why `add_conditional_edges` alone is sufficient here).

A dedicated `"sanitize"` node -- named in the phase doc's explicit
first-commit requirement -- sits between `"tools"` and the routing back to
`"agent"`, calling `sandbox_untrusted_text` unchanged. There is no
intermediate state in this module's history where a tool-result edge
reaches the agent node unsanitized.

`DeterministicPolicyStubLLMClient`/any `StubLLMClient` is held as a
closure variable captured by the node functions built inside
`build_langgraph_triage_graph`, never stored in graph state -- it is a
stateful Python object with private mutable fields, and this graph runs
with no `checkpointer=`, so nothing requires it to be serializable through
a channel (see `.agent/plan.md`'s Rejected alternatives).

Unit 4's `StubLLMClient` adapter is `_stub_llm_adapter`, a module-level
function the `"agent"` node calls with `(llm_client, state["messages"])`.
It performs no message-format translation -- Unit 2 already made
`Message` the state's message type end-to-end, so there is no
LangChain-message shape to convert to or from. It only calls
`llm_client.next_action(messages)` and translates the returned
`AgentAction` (`ToolCallAction`/`FinalAnswerAction`) into the `pending_*`
dict the graph routes on, exactly as `agent_node` did inline before this
unit.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from reachability.index import Verdict, compute_reachability
from reachability.index.reachability_models import ReachabilityResult

from .agent_models import Message, ToolCallRecord, TriageFinding
from .index_adapter import RepoIndex
from .llm_errors import (
    LLMMalformedResponseError,
    LLMRateLimitedError,
    LLMRefusalError,
    LLMTimeoutError,
    LLMTransportError,
    LLMTruncatedError,
)
from .sandbox import sandbox_untrusted_text
from .stub_llm import FinalAnswerAction, StubLLMClient, ToolCallAction
from .termination_cause import (
    BUDGET_EXCEEDED_PREFIX,
    LLM_MALFORMED_RESPONSE_PREFIX,
    LLM_RATE_LIMITED_PREFIX,
    LLM_REFUSAL_PREFIX,
    LLM_TIMEOUT_PREFIX,
    LLM_TRANSPORT_ERROR_PREFIX,
    LLM_TRUNCATED_PREFIX,
    MALFORMED_TOOL_CALL_PREFIX,
    TARGET_NOT_FOUND_REASON,
)
from .tool_dispatch import (
    AgentLoopError,
    _confirmed_id_matches_target,
    _dispatch_tool,
    _is_well_formed_tool_call,
)

# Phase 5 U2: every LLMError subclass the agent node catches, mapped to the
# hard-coded reason-string prefix used by `finalize_llm_error_node` --
# matching the existing convention of `langgraph_loop.py:185`/`:198`/`:220`.
# `GroqConfigError` is deliberately absent: it is a startup/configuration
# failure (missing API key), not a per-call degradation case, and must
# fail client construction, never be caught mid-loop (see `job_runner.py`).
# Phase 6 U1: these prefixes are now imported from `termination_cause.py`,
# the single source of truth also used by `compute_termination_cause` --
# not duplicated here.
_LLM_ERROR_REASON_PREFIXES: dict[type[Exception], str] = {
    LLMTimeoutError: LLM_TIMEOUT_PREFIX,
    LLMRateLimitedError: LLM_RATE_LIMITED_PREFIX,
    LLMTransportError: LLM_TRANSPORT_ERROR_PREFIX,
    LLMRefusalError: LLM_REFUSAL_PREFIX,
    LLMTruncatedError: LLM_TRUNCATED_PREFIX,
    LLMMalformedResponseError: LLM_MALFORMED_RESPONSE_PREFIX,
}


class LangGraphTriageState(TypedDict):
    """Graph state schema. Plain-overwrite (`LastValue`) semantics for
    every field -- each node returns the full updated value, mirroring
    the deleted hand-rolled loop module's local variables exactly, so no
    reducer is needed."""

    messages: list[Message]
    tool_calls: list[ToolCallRecord]
    confirmed_symbol_ids: set[str]
    calls_made: int
    budget: int
    target_module: str
    target_symbol: str | None
    pending_action_kind: str | None
    pending_tool_name: str | None
    pending_tool_arguments: dict[str, object] | None
    pending_final_target_module: str | None
    pending_final_target_symbol: str | None
    pending_final_rationale: str | None
    pending_llm_error_class: str | None
    pending_llm_error_detail: str | None
    raw_tool_result: object | None
    finding: TriageFinding | None


def _stub_llm_adapter(llm_client: StubLLMClient, messages: list[Message]) -> dict:
    """Unit 4's `StubLLMClient` adapter into the graph's model slot.

    Performs no message-format translation -- `messages` is already
    `list[Message]`, the same type the deleted hand-rolled loop module
    used, because Unit 2 made `Message` the state's message type
    end-to-end. This function only
    calls `llm_client.next_action(messages)` and translates the returned
    `AgentAction` into the `pending_*` dict shape `agent_node`'s routing
    functions consume.
    """
    action = llm_client.next_action(messages)

    if isinstance(action, ToolCallAction):
        return {
            "pending_action_kind": "tool_call",
            "pending_tool_name": action.tool_name,
            "pending_tool_arguments": action.arguments,
        }

    if isinstance(action, FinalAnswerAction):
        return {
            "pending_action_kind": "final_answer",
            "pending_final_target_module": action.target_module,
            "pending_final_target_symbol": action.target_symbol,
            "pending_final_rationale": action.rationale,
        }

    raise AgentLoopError(
        f"llm_client.next_action returned neither a ToolCallAction nor a "
        f"FinalAnswerAction: {type(action)!r}"
    )


def build_langgraph_triage_graph(
    llm_client: StubLLMClient, repo_index: RepoIndex
) -> CompiledStateGraph:
    """The single function that owns every `add_node`/`add_edge`/
    `add_conditional_edges` call for this graph -- per Unit 3's
    structural-test fallback requirement in the phase doc. Binds
    `llm_client`/`repo_index` into the node closures; neither is stored in
    graph state."""

    def agent_node(state: LangGraphTriageState) -> dict:
        try:
            return _stub_llm_adapter(llm_client, state["messages"])
        except tuple(_LLM_ERROR_REASON_PREFIXES) as exc:
            return {
                "pending_action_kind": "llm_error",
                "pending_llm_error_class": type(exc).__name__,
                "pending_llm_error_detail": str(exc),
            }

    def tools_node(state: LangGraphTriageState) -> dict:
        tool_name = state["pending_tool_name"]
        arguments = state["pending_tool_arguments"]

        if not _is_well_formed_tool_call(tool_name, arguments):
            raise AgentLoopError(
                f"tools_node entered with a malformed tool call: {tool_name!r} "
                f"-- the routing function should have caught this"
            )

        raw_result = _dispatch_tool(tool_name, arguments, repo_index)
        calls_made = state["calls_made"] + 1

        confirmed_symbol_ids = state["confirmed_symbol_ids"]
        if tool_name == "search_symbol":
            confirmed_symbol_ids = confirmed_symbol_ids | {node.node_id for node in raw_result}

        return {
            "raw_tool_result": raw_result,
            "calls_made": calls_made,
            "confirmed_symbol_ids": confirmed_symbol_ids,
        }

    def sanitize_node(state: LangGraphTriageState) -> dict:
        sanitized = sandbox_untrusted_text(str(state["raw_tool_result"]))
        messages = [*state["messages"], Message(role="tool", content=sanitized)]
        tool_calls = [
            *state["tool_calls"],
            ToolCallRecord(
                sequence=len(state["tool_calls"]),
                tool_name=state["pending_tool_name"],
                arguments=state["pending_tool_arguments"],
                result=sanitized,
            ),
        ]
        return {
            "messages": messages,
            "tool_calls": tool_calls,
            "raw_tool_result": None,
        }

    def finalize_budget_exceeded_node(state: LangGraphTriageState) -> dict:
        finding = TriageFinding(
            result=ReachabilityResult(
                target_module=state["target_module"],
                target_symbol=state["target_symbol"],
                verdict=Verdict.UNKNOWN,
                path=None,
                reason=f"{BUDGET_EXCEEDED_PREFIX}: tool-call budget exhausted before a final answer was reached",
            ),
            rationale="",
            tool_calls=state["tool_calls"],
        )
        return {"finding": finding}

    def finalize_malformed_tool_call_node(state: LangGraphTriageState) -> dict:
        finding = TriageFinding(
            result=ReachabilityResult(
                target_module=state["target_module"],
                target_symbol=state["target_symbol"],
                verdict=Verdict.UNKNOWN,
                path=None,
                reason=f"{MALFORMED_TOOL_CALL_PREFIX}: {state['pending_tool_name']}",
            ),
            rationale="",
            tool_calls=state["tool_calls"],
        )
        return {"finding": finding}

    def finalize_llm_error_node(state: LangGraphTriageState) -> dict:
        error_class_name = state["pending_llm_error_class"]
        detail = state["pending_llm_error_detail"]
        prefix = next(
            (
                p
                for cls, p in _LLM_ERROR_REASON_PREFIXES.items()
                if cls.__name__ == error_class_name
            ),
            "llm_error",
        )
        finding = TriageFinding(
            result=ReachabilityResult(
                target_module=state["target_module"],
                target_symbol=state["target_symbol"],
                verdict=Verdict.UNKNOWN,
                path=None,
                reason=f"{prefix}: {detail}",
            ),
            rationale="",
            tool_calls=state["tool_calls"],
        )
        return {"finding": finding}

    def finalize_answer_node(state: LangGraphTriageState) -> dict:
        target_module = state["pending_final_target_module"]
        target_symbol = state["pending_final_target_symbol"]
        rationale = state["pending_final_rationale"]

        if not any(
            _confirmed_id_matches_target(node_id, target_module, target_symbol)
            for node_id in state["confirmed_symbol_ids"]
        ):
            finding = TriageFinding(
                result=ReachabilityResult(
                    target_module=target_module,
                    target_symbol=target_symbol,
                    verdict=Verdict.UNKNOWN,
                    path=None,
                    reason=TARGET_NOT_FOUND_REASON,
                ),
                rationale=rationale,
                tool_calls=state["tool_calls"],
            )
            return {"finding": finding}

        result = compute_reachability(
            target_module,
            target_symbol,
            repo_index.entrypoints,
            repo_index.edges,
            repo_index.report,
        )
        finding = TriageFinding(result=result, rationale=rationale, tool_calls=state["tool_calls"])
        return {"finding": finding}

    def _route_before_agent(state: LangGraphTriageState) -> Literal["agent", "budget_exceeded"]:
        if state["calls_made"] >= state["budget"]:
            return "budget_exceeded"
        return "agent"

    def _route_after_agent(
        state: LangGraphTriageState,
    ) -> Literal["tools", "malformed", "finalize_answer", "llm_error"]:
        if state["pending_action_kind"] == "llm_error":
            return "llm_error"
        if state["pending_action_kind"] == "final_answer":
            return "finalize_answer"
        if _is_well_formed_tool_call(state["pending_tool_name"], state["pending_tool_arguments"]):
            return "tools"
        return "malformed"

    graph = StateGraph(LangGraphTriageState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("sanitize", sanitize_node)
    graph.add_node("finalize_budget_exceeded", finalize_budget_exceeded_node)
    graph.add_node("finalize_malformed_tool_call", finalize_malformed_tool_call_node)
    graph.add_node("finalize_llm_error", finalize_llm_error_node)
    graph.add_node("finalize_answer", finalize_answer_node)

    graph.set_conditional_entry_point(
        _route_before_agent,
        {"agent": "agent", "budget_exceeded": "finalize_budget_exceeded"},
    )
    graph.add_conditional_edges(
        "agent",
        _route_after_agent,
        {
            "tools": "tools",
            "malformed": "finalize_malformed_tool_call",
            "finalize_answer": "finalize_answer",
            "llm_error": "finalize_llm_error",
        },
    )
    graph.add_edge("tools", "sanitize")
    graph.add_conditional_edges(
        "sanitize",
        _route_before_agent,
        {"agent": "agent", "budget_exceeded": "finalize_budget_exceeded"},
    )
    graph.add_edge("finalize_budget_exceeded", END)
    graph.add_edge("finalize_malformed_tool_call", END)
    graph.add_edge("finalize_llm_error", END)
    graph.add_edge("finalize_answer", END)

    return graph.compile()


def run_triage_loop_langgraph(
    llm_client: StubLLMClient,
    repo_index: RepoIndex,
    target_module: str,
    target_symbol: str | None,
    budget: int,
) -> TriageFinding:
    """Public entry point mirroring the deleted hand-rolled loop module's
    `run_triage_loop` signature (minus the test-only `_on_raw_tool_result`, which stays
    specific to the old loop -- Unit 2 does not need to reproduce that
    hook)."""

    initial_state: LangGraphTriageState = {
        "messages": [
            Message(
                role="user",
                content=f"target_module={target_module!r} target_symbol={target_symbol!r}",
            ),
        ],
        "tool_calls": [],
        "confirmed_symbol_ids": set(),
        "calls_made": 0,
        "budget": budget,
        "target_module": target_module,
        "target_symbol": target_symbol,
        "pending_action_kind": None,
        "pending_tool_name": None,
        "pending_tool_arguments": None,
        "pending_final_target_module": None,
        "pending_final_target_symbol": None,
        "pending_final_rationale": None,
        "pending_llm_error_class": None,
        "pending_llm_error_detail": None,
        "raw_tool_result": None,
        "finding": None,
    }

    graph = build_langgraph_triage_graph(llm_client, repo_index)
    result = graph.invoke(initial_state)
    return result["finding"]
