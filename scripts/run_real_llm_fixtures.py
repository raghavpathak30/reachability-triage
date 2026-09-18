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
"""

import json
import os
import sys
import time
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

RESULTS_DIR = REPO_ROOT / "results" / "real_llm"


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
    }
    start = time.monotonic()
    try:
        target_module = _module_dotted_name(label["sink"]["file"])
        repo_index = build_repo_index(repo_root)
        target_symbol = resolve_target(
            repo_index.symbol_index, target_module, label["sink"]["line"], fixture_dir.name
        )

        client = GroqLLMClient()
        finding = run_triage_loop_langgraph(
            client, repo_index, target_module, target_symbol, budget=EVAL_BUDGET
        )

        row["verdict"] = finding.result.verdict.value
        row["reason"] = finding.result.reason
        row["tool_call_count"] = len(finding.tool_calls)
        row["total_tokens_used"] = client.total_tokens_used
        row["total_cost_accrued"] = client.total_cost_accrued
    except Exception as exc:  # gate requires zero unhandled exceptions
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["elapsed_seconds"] = round(time.monotonic() - start, 4)
    return row


def main() -> int:
    fixture_dirs = discover_eval_fixtures(EVAL_FIXTURES_ROOTS)
    sha = git_sha()
    out_dir = RESULTS_DIR / sha
    out_dir.mkdir(parents=True, exist_ok=True)

    start = time.monotonic()
    rows = []
    for fixture_dir in fixture_dirs:
        row = run_one_fixture(fixture_dir)
        rows.append(row)
        (out_dir / f"{row['id']}.json").write_text(json.dumps(row, indent=2) + "\n")
        status = "ERROR" if row["error"] else row["verdict"]
        print(f"{row['id']:45} {status}")

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
    print(f"per-fixture results written to: {out_dir}")

    return 0 if exception_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
