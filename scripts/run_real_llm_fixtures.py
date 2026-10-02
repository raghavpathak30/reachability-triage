#!/usr/bin/env python3
"""Phase 5 U1 gate script: run every eval fixture through the real Groq
API (`GroqLLMClient`) and confirm nothing crashes.

**Never invoked by CI** (`.github/workflows/tests.yml` has no reference to
this file) -- it costs real money and depends on `GROQ_API_KEY` actually
being set. This is a local, manual gate only. Reuses
`eval_harness`'s own fixture discovery/target-resolution helpers rather
than duplicating them (both live under `src/`, so importing across is the
normal direction here -- unlike `scripts/measure_l5.py`, which
`eval_harness.py` deliberately does not import from, since `scripts/` is
CLI-only and never imported by `src/`).

Writes one JSON file per fixture to
`results/real_llm/<git_sha>/<fixture_id>.json` (gitignored, per U4's
`results/` gitignore) and prints a summary: total tokens, total estimated
cost, wall-clock seconds, and exception count (must be zero to pass this
gate).

Phase 8 additions (diagnosis only, never a gate): each row also records the
termination cause, a normalized content-free tool-call trace and loop metrics
(`reachability.triage.run_trace`), per-request usage, response-shape metadata
(never reply text) and rate-limit events. Opt-in env vars:
`TRIAGE_REAL_RUN_MAX_TOTAL_WAIT_SECONDS` (wait out a 429 inside an
investigation, per client; unset keeps the pre-existing no-wait client) and
`TRIAGE_REAL_RUN_DELAY_SECONDS` (sleep between fixtures, default 0).
Phase 9 U2a probe opt-ins (production code never reads these):
`TRIAGE_REAL_RUN_TOOL_CHOICE` (`auto`/`required`, passed to the client; unset
passes nothing), `TRIAGE_REAL_RUN_FIXTURES` (comma-separated tokens matched
against the first `_`-separated segment of each fixture directory name, e.g.
`06`, `16b`; an unknown token is a hard error before any API call; unset runs
all) and `TRIAGE_REAL_RUN_LABEL` (writes to `results/real_llm_<label>/<sha>`
instead of `results/real_llm/<sha>`, so probes never share the main run's
resume directory). Re-running
resumes: a fixture whose existing row has no error and a non-infrastructure
termination cause is skipped and its row reused. The response cache is
disabled here, so `response_shape_log` covers every model response.
"""

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# This is a local, ad hoc script with no Postgres provisioned for it (unlike
# the pytest suite's session-scoped postgres_cluster fixture) -- U3's
# response cache would otherwise crash on its first call trying to read
# DATABASE_URL. This gate is also about observing a fresh, uncached real
# model response per fixture, not exercising the cache (that's U3's own
# gate, tests/test_llm_cache.py), so disabling it here is correct, not just
# a workaround.
os.environ.setdefault("TRIAGE_LLM_CACHE_DISABLED", "1")

from reachability.agent.eval_harness import (  # noqa: E402
    EVAL_BUDGET,
    EVAL_FIXTURES_ROOTS,
    _module_dotted_name,
    discover_eval_fixtures,
    git_sha,
    load_label,
    resolve_target,
)
from reachability.triage.groq_llm import GroqLLMClient  # noqa: E402
from reachability.triage.index_adapter import build_repo_index  # noqa: E402
from reachability.triage.langgraph_loop import run_triage_loop_langgraph  # noqa: E402
from reachability.triage.run_trace import (  # noqa: E402
    build_tool_call_trace,
    cap_reason,
    loop_metrics,
    redact_org_ids,
)
from reachability.triage.termination_cause import compute_termination_cause  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results" / "real_llm"

_INFRA_CAUSES = {
    "llm_rate_limited",
    "llm_timeout",
    "llm_transport_error",
    "llm_request_too_large",
}


def _make_client() -> GroqLLMClient:
    kwargs: dict = {}
    max_wait = os.environ.get("TRIAGE_REAL_RUN_MAX_TOTAL_WAIT_SECONDS")
    if max_wait is not None:
        kwargs["wait_on_rate_limit"] = True
        kwargs["rate_limit_max_total_wait_seconds"] = float(max_wait)
    tool_choice = os.environ.get("TRIAGE_REAL_RUN_TOOL_CHOICE")
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    return GroqLLMClient(**kwargs)


def _filter_fixtures(fixture_dirs: list[Path]) -> list[Path]:
    raw = os.environ.get("TRIAGE_REAL_RUN_FIXTURES")
    if raw is None:
        return fixture_dirs
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    known = {d.name.split("_", 1)[0] for d in fixture_dirs}
    unknown = [t for t in tokens if t not in known]
    if unknown:
        raise SystemExit(f"TRIAGE_REAL_RUN_FIXTURES: unknown fixture token(s): {unknown}")
    return [d for d in fixture_dirs if d.name.split("_", 1)[0] in tokens]


def _load_resumable(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    cause = row.get("termination_cause")
    if row.get("error") is not None or cause is None or cause in _INFRA_CAUSES:
        return None
    return row


def run_one_fixture(fixture_dir: Path) -> dict:
    label = load_label(fixture_dir)
    repo_root = fixture_dir / "repo"

    row: dict = {
        "id": fixture_dir.name,
        "verdict": None,
        "reason": None,
        "tool_call_count": None,
        "total_tokens_used": None,
        "total_cost_accrued": None,
        "elapsed_seconds": None,
        "error": None,
        "termination_cause": None,
        "budget": EVAL_BUDGET,
        "allowed_verdicts": label["allowed_verdicts"],
        "pass": None,
        "tool_call_trace": None,
        "loop_metrics": None,
        "request_usage_log": None,
        "response_shape_log": None,
        "name_normalization_events": None,
        "tool_choice": None,
        "rate_limit_events": None,
        "total_rate_limit_wait_seconds": None,
    }
    start = time.monotonic()
    try:
        target_module = _module_dotted_name(label["sink"]["file"])
        repo_index = build_repo_index(repo_root)
        target_symbol = resolve_target(
            repo_index.symbol_index, target_module, label["sink"]["line"], fixture_dir.name
        )

        client = _make_client()
        finding = run_triage_loop_langgraph(
            client, repo_index, target_module, target_symbol, budget=EVAL_BUDGET
        )

        row["verdict"] = finding.result.verdict.value
        row["reason"] = cap_reason(finding.result.reason)
        row["tool_call_count"] = len(finding.tool_calls)
        row["total_tokens_used"] = client.total_tokens_used
        row["total_cost_accrued"] = client.total_cost_accrued
        row["termination_cause"] = compute_termination_cause(finding).value
        row["pass"] = row["verdict"] in label["allowed_verdicts"]
        row["tool_call_trace"] = build_tool_call_trace(finding.tool_calls)
        row["loop_metrics"] = loop_metrics(finding.tool_calls)
        row["request_usage_log"] = list(client.request_usage_log)
        row["response_shape_log"] = list(client.response_shape_log)
        row["name_normalization_events"] = list(client.name_normalization_events)
        row["tool_choice"] = client.tool_choice
        row["rate_limit_events"] = list(client.rate_limit_events)
        row["total_rate_limit_wait_seconds"] = client.total_rate_limit_wait_seconds
    except Exception as exc:  # gate requires zero unhandled exceptions
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["elapsed_seconds"] = round(time.monotonic() - start, 4)
    return row


def main() -> int:
    fixture_dirs = discover_eval_fixtures(EVAL_FIXTURES_ROOTS)
    fixture_dirs = _filter_fixtures(fixture_dirs)
    sha = git_sha()
    label = os.environ.get("TRIAGE_REAL_RUN_LABEL")
    if label:
        out_dir = REPO_ROOT / "results" / f"real_llm_{label}" / sha
    else:
        out_dir = RESULTS_DIR / sha
    out_dir.mkdir(parents=True, exist_ok=True)

    start = time.monotonic()
    rows = []
    delay_seconds = float(os.environ.get("TRIAGE_REAL_RUN_DELAY_SECONDS", "0"))
    for index, fixture_dir in enumerate(fixture_dirs):
        existing = _load_resumable(out_dir / f"{fixture_dir.name}.json")
        if existing is not None:
            rows.append(existing)
            print(f"{fixture_dir.name:45} already recorded, skipping")
            continue
        row = run_one_fixture(fixture_dir)
        rows.append(row)
        (out_dir / f"{row['id']}.json").write_text(
            json.dumps(redact_org_ids(row), indent=2) + "\n"
        )
        status = "ERROR" if row["error"] else row["verdict"]
        print(f"{row['id']:45} {status}")
        if delay_seconds > 0 and index < len(fixture_dirs) - 1:
            time.sleep(delay_seconds)

    elapsed = round(time.monotonic() - start, 2)
    total_tokens = sum(r["total_tokens_used"] or 0 for r in rows)
    total_cost = sum(r["total_cost_accrued"] or 0.0 for r in rows)
    exception_count = sum(1 for r in rows if r["error"] is not None)

    print()
    print(f"fixtures run: {len(rows)}")
    print(f"total tokens: {total_tokens}")
    print(f"total estimated cost (USD): {total_cost:.6f}")
    print(f"wall-clock seconds: {elapsed}")
    print(f"exception count: {exception_count}")
    tally = Counter(r.get("termination_cause") or "error" for r in rows)
    print("termination causes: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    print(f"per-fixture results written to: {out_dir}")

    return 0 if exception_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
