"""U5: wires U2 (`acquire_source`) -> U1 (`build_repo_index`) -> U3/U4
(`run_triage_loop`) into one synchronous, per-job function that `main.py`
dispatches via `BackgroundTasks`.

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
2. **Production "LLM" client** -- `DeterministicPolicyStubLLMClient`
   (`stub_llm.py`), instantiated fresh per job. **This is not a real LLM.**
   It is a deterministic placeholder policy (confirm target, backward-BFS
   callers, answer) used until a later unit adds live LLM integration --
   live LLM API integration is still NOT BUILT per project `CLAUDE.md`.
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

`run_triage_job` always calls `run_triage_loop(..., _on_raw_tool_result=None)`
-- that parameter is test-only instrumentation (see `agent_loop.py`'s
docstring and `DECISIONS.md`'s U3/U4 addendum) and must never be wired to a
non-`None` value from any production code path.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .acquisition import acquire_source
from .agent_loop import run_triage_loop
from .index_adapter import build_repo_index
from .sandbox import sandbox_untrusted_text
from .stub_llm import DeterministicPolicyStubLLMClient

if TYPE_CHECKING:
    from main import TriageRequest

DEFAULT_TOOL_CALL_BUDGET = 30

_MAX_ERROR_LENGTH = 2000


class EmptyRepoIndexError(RuntimeError):
    """Raised when `build_repo_index` succeeds but finds zero modules.

    See this module's docstring, decision 5, for the rationale and the
    accepted known limitation (a genuine zero-`.py`-module package also
    triggers this).
    """


def _sanitize_error(exc: Exception) -> str:
    """Build the stored `error` string for a failed job.

    `f"{type(exc).__name__}: {exc}"`, passed through `sandbox_untrusted_text`
    (see this module's docstring, decision 6), then truncated to
    `_MAX_ERROR_LENGTH` characters.
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

    try:
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
            llm_client = DeterministicPolicyStubLLMClient(
                request.target_module, request.target_symbol
            )
            finding = run_triage_loop(
                llm_client,
                repo_index,
                request.target_module,
                request.target_symbol,
                budget,
                _on_raw_tool_result=None,
            )
    except Exception as exc:
        record["error"] = _sanitize_error(exc)
        record["status"] = "failed"
    else:
        record["finding"] = finding
        record["status"] = "completed"
