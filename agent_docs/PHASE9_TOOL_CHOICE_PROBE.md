# Phase 9 U2 — `tool_choice` probe

- **Run:** 2026-10-02 19:42:54 UTC (`/tmp/p9_probe_meta.log`); code under test `4ef15866ce33108055c322717a9dafa83d881a9c` (phase9 U2a); model `openai/gpt-oss-20b` (`src/reachability/triage/llm_config.py:24`); `TRIAGE_LLM_CACHE_DISABLED=1`; wait-on-429 cap 900 s, 10 s between fixtures.
- **Design:** 13 fixtures (01, 02, 05, 06, 08, 09, 10, 11, 16, 21, 22, 23, 24), one run per arm. Arm A = `tool_choice="auto"` (`results/real_llm_probe_auto/<sha>/`), arm R = `tool_choice="required"` (`results/real_llm_probe_required/<sha>/`). Both arms run with U1 (the exact `functions.` prefix strip) active. Cost: A 54,296 tokens / est. $0.0087, R 52,115 tokens / est. $0.0084.
- **Rule applied:** the probe adoption rule frozen in `DECISIONS.md` §17 (committed in `3afca96`, before this run), R1–R5 as written.

## 1. R1–R5 (as frozen)

| rule | result | evidence |
|---|---|---|
| R1 supported | **holds** | R has no `llm_transport_error`/`llm_request_too_large` row, no row `error`, and no 400/422 in any R `reason` or `rate_limit_events`. `tool_choice` recorded as `required` on all 13 R rows. |
| R2 shape B fixed | **holds** (probe informative) | A has **6** text replies (13 rows; `finish_reason=stop`, 0 tool calls); R has **0**. Fisher exact, two-sided, 6/13 vs 0/13: p = 0.015 (informational, not part of the rule). |
| R3 no new malformed class | **holds** | R has 0 `llm_malformed_response` rows (A has 6, all text replies). |
| R4 no increase in calls | **holds** | sum of `tool_call_count`: A 39, R 37 (ratio 0.95 <= 1.25); no R row `budget_exceeded` or `repeated_calls >= 5`; all 13 R rows FINISHED. |
| R5 no accuracy loss | **holds** | no R row with a verdict outside its label's allowed set; 0 G1 violations; no new `target_not_found`. |

**Outcome under the frozen rule: Branch A (adopt `required`)** — all of R1–R5 hold. The plan requires the human to confirm the branch before any code changes the production default; this doc does not adopt it.

## 2. Per fixture

| fixture | A cause / verdict / calls / text replies | R cause / verdict / calls / text replies | label allows |
|---|---|---|---|
| 01 | malformed / unknown / 3 / 1 | completed / reachable / 4 / 0 | reachable |
| 02 | completed / reachable / 5 / 0 | completed / reachable / 4 / 0 | reachable |
| 05 | completed / reachable / 3 / 0 | completed / reachable / 2 / 0 | reachable |
| 06 | malformed / unknown / 3 / 1 | completed / reachable / 3 / 0 | reachable |
| 08 | malformed / unknown / 3 / 1 | completed / reachable_only_from_tests / 3 / 0 | reachable_only_from_tests |
| 09 | completed / not_reachable / 3 / 0 | completed / not_reachable / 2 / 0 | not_reachable |
| 10 | malformed / unknown / 4 / 1 | completed / not_reachable / 2 / 0 | not_reachable |
| 11 | completed / not_reachable / 4 / 0 | completed / not_reachable / 3 / 0 | not_reachable |
| 16 | unclassified / unknown / 2 / 0 | unclassified / unknown / 3 / 0 | unknown, reachable |
| 21 | completed / not_reachable / 2 / 0 | completed / not_reachable / 2 / 0 | not_reachable |
| 22 | malformed / unknown / 2 / 1 | unclassified / unknown / 3 / 0 | unknown |
| 23 | unclassified / unknown / 3 / 0 | unclassified / unknown / 3 / 0 | unknown |
| 24 | malformed / unknown / 2 / 1 | completed / not_reachable / 3 / 0 | not_reachable |

Cause counts — A: 5 completed, 2 unclassified, 6 `llm_malformed_response`; R: 10 completed, 3 unclassified, 0 malformed.

## 3. What U1 contributed

Prefix-strip events (`name_normalization_events`): **A 4, R 7.** The `functions.` prefix still appears under `required`, so U1 is needed under either `tool_choice`; without it R3 would not hold. All 6 remaining A-arm malformed rows are text replies (finish_reason `stop`, 277–1,048 chars of content, 0 tool calls) on the turn after 2–4 evidence calls. Which fixtures reply in text is not stable across runs: of the 7 Phase 8 shape-B fixtures (06, 08, 16, 21, 22, 23, 24), four did it again here (06, 08, 22, 24), and two fixtures that were shape A in Phase 8 (01, 10) did it here. So the text-reply behaviour is stochastic per run, not per fixture.

## 4. Every definite verdict vs its label (both arms)

All **15** definite (`completed`) rows (5 in A, 10 in R) have a verdict inside the label's allowed set. **No confident wrong verdict occurred in either arm.** Each also equals what `compute_reachability` returns for the assigned target when run directly on the fixture (no model; see the snippet), so none depends on the model.

The hypothesis checked: fixture 21 returned `not_reachable` in both arms. Its label allows exactly that (`tests/fixtures/l5_phase4/21_attribute_chain_segment_escape/label.json`: `allowed_verdicts == ["not_reachable"]`, `forbidden_verdicts == ["reachable", "reachable_only_from_tests", "unknown"]`). So it is correct per label, not a wrong verdict. The same holds for 10 and 24: both labels allow only `not_reachable` (and forbid `unknown`), so R's `not_reachable` is correct. A's `unknown` on 10 and 24 was a malformed text reply (an `unknown` the label forbids, though it is never a confident verdict).

How the index reaches `not_reachable` for the assigned target (deterministic recomputation, snippet last block):

| fixture | assigned target | entrypoints | edges into target | result |
|---|---|---|---|---|
| 21 | `pkg.target:probe` | 1 | 0 | `no call path found from any entrypoint to pkg.target:probe` |
| 10 | `pkg.real:vulnerable` | 1 | 0 | `no call path found from any entrypoint to pkg.real:vulnerable` |
| 24 | `pkg.orphan2:orphan_target` | 1 | 0 | `no call path found from any entrypoint to pkg.orphan2:orphan_target` |

In each case there is an entrypoint, no call edge reaches the target, and no unresolved-name bridge applies, so the BFS finds no path. Fixture 21 is `not_reachable` by design: `DECISIONS.md` §5.3 narrows the escape check so an attribute-chain segment named `probe` is not recorded as an escape.

**Named target vs assigned target.** For the 8 `not_reachable` rows (A: 09, 11, 21; R: 09, 10, 11, 21, 24) the model's named final target is recoverable from the row's `reason` and equals the assigned target in every one. For the 7 `reachable`/`reachable_only_from_tests` rows (A: 02, 05; R: 01, 02, 05, 06, 08) it is **not recorded**: `scripts/run_real_llm_fixtures.py` rows carry no `final_target_module`/`final_target_symbol` (the injection script's rows do, `scripts/run_injection_suite_real.py:221-222`). The verdict agrees with the deterministic result for the assigned target, but the doc cannot show that the model named it. Reason this matters: the loop scores the target the model *names*, provided `search_symbol` confirmed it (`langgraph_loop.py:278-300` via `tool_dispatch.py:62`), not the assigned one.

## 5. Open items from this probe

1. **Clean-run rows do not record the named final target** (above). Proposed for U3 tooling, before the real runs: add `final_target_module`, `final_target_symbol` and a `named_target_matches_assigned` boolean to `scripts/run_real_llm_fixtures.py` rows (additive; the injection script already has the first two). Needs the orchestrator's go-ahead since it is outside the plan's step list.
2. **Wrong verdict in both arms:** none found, so there is nothing to record or fix (the check you asked for is section 4).
3. A `required` default makes every production Groq request send `tool_choice="required"`; cached `"auto"` rows are orphaned (plan N4), and the stub lane is unaffected.

## 6. Limitations

One run per arm per fixture; model non-determinism is visible (section 3). The 13 fixtures were chosen largely from Phase 8 failures (critique N2), so R2's evidence is conditional on that selection and says nothing about the 17 fixtures not probed. R is judged on 13 runs with 0 text replies; zero of 13 bounds the rate loosely. R2's A-arm precondition was met (6 text replies), so no probe re-run was needed. No raw reply text, tool-result text or rationale is stored or quoted here.

## Analysis snippet (verbatim; run from the worktree root)

```python
import glob, json, pathlib, re, sys
from collections import Counter
sys.path[:0] = ["src", "scripts", "."]
import run_real_llm_fixtures as rf
from reachability.agent.eval_harness import resolve_target
from reachability.index import compute_reachability
from reachability.triage.index_adapter import build_repo_index

SHA = "4ef15866ce33108055c322717a9dafa83d881a9c"
INFRA = {"llm_rate_limited", "llm_timeout", "llm_transport_error", "llm_request_too_large"}
FIN = {"completed", "unclassified", "target_not_found"}
load = lambda arm: {json.load(open(f))["id"]: json.load(open(f))
                    for f in sorted(glob.glob(f"results/real_llm_probe_{arm}/{SHA}/*.json"))}
A, R = load("auto"), load("required")
assert set(A) == set(R) and len(A) == 13

def text_replies(r):
    return sum(1 for s in r["response_shape_log"]
               if s["tool_call_count"] == 0 or s["finish_reason"] == "stop")

def finished(r): return r["error"] is None and r["termination_cause"] in FIN
def n(r): return r["loop_metrics"]["tool_call_count"]

print("causes  auto", dict(Counter(r["termination_cause"] for r in A.values())),
      " required", dict(Counter(r["termination_cause"] for r in R.values())))
print("tool_choice recorded  auto", {r["tool_choice"] for r in A.values()},
      " required", {r["tool_choice"] for r in R.values()})

# ---- R1..R5, exactly as frozen
r1 = (not any(r["termination_cause"] in {"llm_transport_error", "llm_request_too_large"}
              or r["error"] is not None for r in R.values())
      and not any(re.search(r"\b(400|422)\b", json.dumps([r["reason"], r["rate_limit_events"]]))
                  for r in R.values()))
a_text = sum(text_replies(r) for r in A.values()); r_text = sum(text_replies(r) for r in R.values())
r2 = (r_text == 0) if a_text >= 1 else None          # None = uninformative
r3 = sum(r["termination_cause"] == "llm_malformed_response" for r in R.values()) == 0
sumA, sumR = sum(n(r) for r in A.values()), sum(n(r) for r in R.values())
r4 = (sumR <= 1.25 * sumA
      and not any(r["termination_cause"] == "budget_exceeded" or r["loop_metrics"]["repeated_calls"] >= 5
                  for r in R.values())
      and all(finished(r) for r in R.values()))
wrong_R = [i for i, r in R.items() if r["verdict"] not in r["allowed_verdicts"]]
tnf_R = [i for i, r in R.items() if r["termination_cause"] == "target_not_found"
         and A[i]["termination_cause"] != "target_not_found"]
r5 = not wrong_R and not tnf_R
print(f"R1 {r1} | R2 {r2} (auto text replies {a_text}, required {r_text}) | R3 {r3} | "
      f"R4 {r4} (sum calls auto {sumA}, required {sumR}, ratio {sumR / sumA:.2f}) | "
      f"R5 {r5} (wrong {wrong_R}, new target_not_found {tnf_R})")
verdicts = {"R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": r5}
print("branch:", "A" if all(v is True for v in verdicts.values()) else
      ("uninformative" if r2 is None else "B"), "| failing:", [k for k, v in verdicts.items() if v is False])

# ---- per-fixture table
print("\nfixture  | auto: cause / verdict / calls / text | required: cause / verdict / calls / text | allowed")
for i in sorted(A):
    a, r = A[i], R[i]
    print(f"{i[:4]:5}| {a['termination_cause']:22} {a['verdict']:26} {n(a)} {text_replies(a)} | "
          f"{r['termination_cause']:11} {r['verdict']:26} {n(r)} {text_replies(r)} | {r['allowed_verdicts']}")

# ---- malformed shapes in the auto arm (kinds only; no text)
print("\nauto-arm malformed rows:")
for i, r in A.items():
    if r["termination_cause"] == "llm_malformed_response":
        last = r["response_shape_log"][-1]
        print(" ", i[:4], {k: last[k] for k in ("finish_reason", "content_length", "tool_call_count", "tool_names", "name_normalized")},
              "prior_calls", n(r))
print("name_normalization_events  auto", sum(len(r["name_normalization_events"]) for r in A.values()),
      " required", sum(len(r["name_normalization_events"]) for r in R.values()))

# ---- accuracy of every definite (completed) verdict in both arms vs label, plus
# a deterministic recomputation for the ASSIGNED target (no model involved).
def assigned(i):
    d = pathlib.Path(glob.glob(f"tests/fixtures/l5*/{i}")[0])
    lab = json.load(open(d / "label.json"))
    tm = rf._module_dotted_name(lab["sink"]["file"])
    idx = build_repo_index(d / "repo")
    ts = resolve_target(idx.symbol_index, tm, lab["sink"]["line"], d.name)
    res = compute_reachability(tm, ts, idx.entrypoints, idx.edges, idx.report)
    return tm, ts, res, idx
print("\ndefinite verdicts vs label, and vs deterministic result for the assigned target:")
for arm, rows in (("auto", A), ("required", R)):
    for i, r in rows.items():
        if r["termination_cause"] != "completed": continue
        tm, ts, res, idx = assigned(i)
        named = re.search(r"to (\S+?:\S+)$", r["reason"] or "")
        print(f"  {arm:8} {i[:4]:5} verdict {r['verdict']:26} in_allowed {r['verdict'] in r['allowed_verdicts']!s:5} "
              f"det(assigned) {res.verdict.value:26} same {r['verdict'] == res.verdict.value!s:5} "
              f"named_in_reason {'=assigned' if named and named.group(1) == f'{tm}:{ts}' else (named.group(1) if named else 'n/a (not recorded)')}")
print("\nhow the index reaches the verdict for the assigned target (fixtures 21, 10, 24):")
for i in sorted(A):
    if i[:2] not in ("21", "10", "24"): continue
    tm, ts, res, idx = assigned(i)
    callers = [e for e in idx.edges if e.callee_id and e.callee_id.endswith(f":{ts}") and tm in e.callee_id]
    print(f"  {i[:4]} assigned {tm}:{ts} | verdict {res.verdict.value} | reason {res.reason!r} | "
          f"entrypoints {len(idx.entrypoints)} | edges into target {len(callers)} | "
          f"unresolved-named-call bridge? {'unknown' in (res.reason or '')}")
```
