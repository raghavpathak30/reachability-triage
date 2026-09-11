"""Value types shared by the U3/U4 triage agent loop.

These are plain, frozen dataclasses -- no behavior, no I/O -- following the
`RepoIndex` precedent in `src/reachability/triage/index_adapter.py`. They
exist so the loop (`agent_loop.py`) and the stub LLM clients
(`stub_llm.py`) can pass structured data between each other without either
side depending on the other's internals.

Per `agent_docs/PHASE2_TRIAGE_AGENT.md:52-57`, `TriageFinding` is the
mechanism that keeps the project's "evidence, never a bare true/false"
convention (CLAUDE.md Conventions) intact once an LLM is in the loop: the
verdict (`result: ReachabilityResult`) always comes from
`compute_reachability`, never from anything the LLM said. `rationale` is
commentary the agent produced to explain itself -- it is never consulted
to derive or override the verdict, and no code in this package should ever
read `rationale` to decide anything.
"""

from dataclasses import dataclass, field

from reachability.index.reachability_models import ReachabilityResult


@dataclass(frozen=True)
class Message:
    """One entry in the agent loop's running context.

    `role` is one of `"system"`, `"user"`, `"assistant"`, `"tool"` (spec
    lines 148-150). Tool-call *results* must only ever be appended with
    `role="tool"` -- see `agent_loop.py`'s `_append_tool_result`, the one
    call site that constructs a `Message` from a tool result.
    """

    role: str
    content: str


@dataclass(frozen=True)
class ToolCallRecord:
    """One sequenced entry in a `TriageFinding`'s tool-call audit trail.

    Field names (`sequence`, `tool_name`, `arguments`, `result`) are this
    plan's own choice -- the spec ("tool name, args, result",
    `agent_docs/PHASE2_TRIAGE_AGENT.md:56`) does not pin exact field names
    (see `.agent/plan.md`'s Risks section). `result` is always the
    *sanitized* string -- i.e. always post-`sandbox_untrusted_text` -- never
    the raw tool return value; nothing in this package stores the raw
    result on a `ToolCallRecord`.
    """

    sequence: int
    tool_name: str
    arguments: dict[str, object]
    result: str


@dataclass(frozen=True)
class TriageFinding:
    """The triage agent loop's final output for one job.

    `result` is the `ReachabilityResult` produced by `compute_reachability`
    -- the sole source of the verdict. `rationale` is the agent's own
    free-text explanation of its reasoning; it is commentary only and is
    never read back to derive, corroborate, or override `result.verdict`.
    `tool_calls` is the full sequenced trace of every tool invocation made
    while producing this finding, in execution order.
    """

    result: ReachabilityResult
    rationale: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
