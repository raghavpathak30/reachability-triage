"""U5: wires U2 (`acquire_source`) -> U1 (`build_repo_index`) ->
`langgraph_loop.py::run_triage_loop_langgraph` into one synchronous,
per-job function that `main.py` dispatches via `BackgroundTasks`.

`run_triage_job` never raises: every exception anywhere in the
acquire/index/run chain -- including this module's own `EmptyRepoIndexError`
-- is caught by a single broad `except Exception` clause and turned into a
`FAILED` record with a sanitized `error` string. This is a deliberate,
single-clause design (not per-exception-type branches): differentiating
`AcquisitionError` vs. `IndexBuildError` vs. an unanticipated bug only
changes the diagnostic prefix embedded in the exception's own `str()`, never
the control flow, so one `except` clause is simpler and more clearly
guarantees the "never stuck in RUNNING" gate property than several would.

Six decisions this module embodies (mirrored into `DECISIONS.md`'s §7 U5
addendum):

1. **target_module/target_symbol source** -- taken directly from the
   `TriageRequest` the caller submitted (`request.target_module`,
   `request.target_symbol`), now required/optional fields on that model.
   A real caller (e.g. triaging a CVE advisory) always names a specific
   vulnerable symbol; this module never derives a target automatically.
2. **Production "LLM" client** -- **Phase 7 status note (supersedes the
   Phase 5 U5 text as originally written): the client is selected per job
   by the `TRIAGE_LLM_MODE` env var (`resolve_llm_mode` /
   `_build_llm_client` below).** Unset or `groq` constructs the real
   `GroqLLMClient` (`groq_llm.py`), instantiated fresh per job, exactly as
   Phase 5 U5 did; `stub` constructs `DeterministicPolicyStubLLMClient`
   (`stub_llm.py`), which docker compose opts into by default so the stack
   runs with no Groq key; any other value fails the job. The Phase 5 swap
   to Groq was made with `agent_docs/PHASE5_INJECTION_REAL_MODEL.md`'s
   real-model injection gate NOT met (see DECISIONS.md sections 13-14) --
   real-model injection resistance is unclaimed.
3. **Tool-call budget** -- `DEFAULT_TOOL_CALL_BUDGET = 30`, fixed for every
   job in this unit; not exposed on `TriageRequest`.
4. **Workdir lifecycle** -- `run_triage_job` opens exactly one
   `tempfile.TemporaryDirectory(prefix="reachability-triage-",
   ignore_cleanup_errors=True)` per job, scoped to the whole chain, and
   passes its path to `acquire_source`. `ignore_cleanup_errors=True` is
   required, not optional: it makes `tempfile` itself suppress a
   `shutil.rmtree` cleanup failure during `__exit__` (e.g. an undeletable
   file extracted from a wheel) instead of raising one, so a cleanup-time
   failure can never masquerade as a chain failure.
5. **Empty `RepoIndex` -> FAILED** -- `build_repo_index` succeeding with
   zero modules (`len(repo_index.report.modules) == 0`) is treated as a
   `FAILED` job via `EmptyRepoIndexError`, raised from inside the same
   `try` block so it is caught by the same `except Exception` clause as any
   other chain failure. Rationale: an empty index is far more likely a
   silent acquisition/extraction defect than a genuinely empty package, and
   letting it proceed to `run_triage_loop` would present that defect as an
   indistinguishable, confident-looking `COMPLETED`/`NOT_REACHABLE` or
   `COMPLETED`/`UNKNOWN` -- exactly the "confident wrong answer" failure
   class this whole project is designed to avoid. **Accepted known
   limitation**: a genuinely pure-native/C-extension wheel with zero `.py`
   modules will also FAIL under this rule, since `EmptyRepoIndexError`
   cannot distinguish "acquisition/extraction defect" from "package
   genuinely has no Python source" -- both present identically as a
   zero-module `RepoIndex`. (User sign-off, this conversation.)
6. **Error message detail** -- the stored `error` string is built as
   `f"{type(exc).__name__}: {exc}"`, passed through the existing
   `sandbox_untrusted_text` (`sandbox.py`) to strip absolute paths and
   high-entropy tokens, then truncated to `_MAX_ERROR_LENGTH` characters.
   `sandbox_untrusted_text` was built for a different threat model
   (prompt-injection redaction of untrusted tool-result text fed back to an
   LLM context), not HTTP-response sanitization; this is the first unit
   reusing it for that second purpose, and its coverage for this new
   purpose is not assumed but was checked against real captured exceptions
   (see `tests/test_triage_job_runner.py`'s
   `test_sanitize_error_against_real_captured_exceptions`).

`record["status"]` is set via the plain string literals
`"running"`/`"completed"`/`"failed"`, never via `from main import
TriageStatus` -- `TriageStatus(str, Enum)` (`main.py`) accepts these
strings transparently on both dict storage and pydantic serialization, so
no runtime import of `main` is needed at all. Only `if TYPE_CHECKING: from
main import TriageRequest` is used, for typing only, mirroring
`acquisition.py`'s existing precedent.

`run_triage_job` always calls `run_triage_loop_langgraph` unconditionally --
there is no `_on_raw_tool_result`-style test-only hook on that function's
signature (see `langgraph_loop.py`'s docstring and `DECISIONS.md`'s
Phase LangGraph addendum).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ..agent.prompt_registry import PROMPT_VERSION
from .acquisition import acquire_source
from .groq_llm import GroqLLMClient
from .index_adapter import build_repo_index
from .langgraph_loop import run_triage_loop_langgraph
from .sandbox import sandbox_untrusted_text
from .stub_llm import DeterministicPolicyStubLLMClient

if TYPE_CHECKING:
    from main import TriageRequest

DEFAULT_TOOL_CALL_BUDGET = 30

_MAX_ERROR_LENGTH = 2000

LLM_MODES = ("groq", "stub")


def resolve_llm_mode() -> str:
    """Resolve `TRIAGE_LLM_MODE` (unset or empty -> `groq`). Single source
    of truth shared with `worker.py::main`. Raises `ValueError` for any
    value other than `groq`/`stub` -- no silent fallback.
    """
    raw = os.environ.get("TRIAGE_LLM_MODE")
    if raw is None or raw == "":
        return "groq"
    if raw not in LLM_MODES:
        raise ValueError(
            f"TRIAGE_LLM_MODE must be one of {', '.join(LLM_MODES)} (got {raw!r})"
        )
    return raw


def _build_llm_client(mode: str, request: "TriageRequest"):
    if mode == "stub":
        return DeterministicPolicyStubLLMClient(
            request.target_module, request.target_symbol
        )
    return GroqLLMClient()


class EmptyRepoIndexError(RuntimeError):
    """Raised when `build_repo_index` succeeds but finds zero modules.

    See this module's docstring, decision 5, for the rationale and the
    accepted known limitation (a genuine zero-`.py`-module package also
    triggers this).
    """


def sanitize_error(exc: Exception) -> str:
    """Build the stored `error` string for a failed job.

    `f"{type(exc).__name__}: {exc}"`, passed through `sandbox_untrusted_text`
    (see this module's docstring, decision 6), then truncated to
    `_MAX_ERROR_LENGTH` characters.

    Public (not a leading-underscore name) so `worker.py` can reuse it for failures
    that occur outside `run_triage_job`'s own try/except (e.g. a malformed
    stored `target`) rather than duplicating this sanitization logic.
    """
    message = f"{type(exc).__name__}: {exc}"
    sanitized = sandbox_untrusted_text(message)
    if len(sanitized) > _MAX_ERROR_LENGTH:
        sanitized = sanitized[:_MAX_ERROR_LENGTH] + "... [truncated]"
    return sanitized


def run_triage_job(
    record: dict,
    request: "TriageRequest",
    budget: int = DEFAULT_TOOL_CALL_BUDGET,
) -> None:
    """Run one triage job end-to-end and mutate `record` in place through
    `RUNNING -> COMPLETED/FAILED`. Never raises -- see this module's
    docstring for the full exception-handling contract.
    """
    record["status"] = "running"
    record["llm_mode"] = None
    llm_client = None

    try:
        record["llm_mode"] = resolve_llm_mode()
        with tempfile.TemporaryDirectory(
            prefix="reachability-triage-", ignore_cleanup_errors=True
        ) as workdir:
            source_path = acquire_source(request, Path(workdir))
            repo_index = build_repo_index(source_path)
            if len(repo_index.report.modules) == 0:
                raise EmptyRepoIndexError(
                    f"build_repo_index succeeded but found zero modules "
                    f"under {source_path.name}"
                )
            # Phase 7: client chosen by TRIAGE_LLM_MODE; unset means the
            # real GroqLLMClient (see this module's docstring, decision 2).
            llm_client = _build_llm_client(record["llm_mode"], request)
            finding = run_triage_loop_langgraph(
                llm_client,
                repo_index,
                request.target_module,
                request.target_symbol,
                budget,
            )
    except Exception as exc:
        record["error"] = sanitize_error(exc)
        record["status"] = "failed"
    else:
        record["finding"] = finding
        record["status"] = "completed"
    finally:
        # Phase 5 U3: client-agnostic persistence plumbing -- works for both
        # GroqLLMClient (real totals) and DeterministicPolicyStubLLMClient
        # (both attributes absent, so this records None/None), so the
        # eventual U5 client swap needs no change here.
        record["token_count"] = getattr(llm_client, "total_tokens_used", None)
        record["total_cost"] = getattr(llm_client, "total_cost_accrued", None)
        record["model_string"] = getattr(llm_client, "model", None)
        record["prompt_version"] = PROMPT_VERSION
