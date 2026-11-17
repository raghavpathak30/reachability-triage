# Council: v1.0.0 final-push plan (2026-11-16)

> All five advisors are the same model in different roles. That gives different framings, not different knowledge, so it will not catch an error the model is systematically confident about.

## Frame

- **Decision.** Adopt the drafted v1.0.0 plan for reachability-triage, or change it.
- **Options.**
  - **A**: the plan as drafted. Ten working days, a CLI-only scanner plus a v0.1 symbol dataset, launch on Dec 1.
  - **B**: a lean 7-day cut. Drop Pipfile/pylock, SARIF and the GitHub Action.
  - **C**: dataset first. The dataset and benchmark are primary; the scanner is a minimal reference consumer.
  - **D** (added): A plus a scoped A7 edge-resolution unit.
- **Constraints.**
  - Time: 4-5 hours a day, 6 days a week, one student.
  - Process: all code goes through `/loop` with a human gate.
  - Correctness: never a false `not_reachable`; claims are pre-registered.
  - Starting point: A1a is built, A1b is at its gate, 871 tests pass, no packaging exists, and the PHASE_APP_SCAN §4 eval rules are frozen.

Inputs:
- Plan draft: session scratchpad `v1_plan.md`.
- Supporting documents:
  - `reports/Python CVE reachability tool gap.md`
  - `agent_docs/PHASE_APP_SCAN.md`
  - `agent_docs/research/APP_SCAN_RESEARCH.md`
  - `DECISIONS.md` §26-27

## Round 1 (independent)

| Advisor | Verdict | Key reasons | Would change mind |
|---|---|---|---|
| Security | A, with security work inside the existing gates | S1 fetch needs A2's archive rules. Today `acquisition.py:125-126` runs a bare `extractall` and `build.py:20` catches only `SyntaxError`. Check the hash before extracting. Fetch from a fixed host. `db update` needs size caps and an atomic write. Advisory text in SARIF and the terminal must be sanitised. The Action must never use `pull_request_target`. | Cutting the Action removes the fork-PR attack surface |
| Reliability | A, with the Action moved to 1.0.1 and the time spent on hardening | One failing dependency becomes a Gap, not a failed scan. Exit codes separate tool errors from findings. `--max-deps`/`--timeout` truncate deterministically. Add `--offline`, retries, a stale-cache warning, `--verbose` timings and a loud no-lockfile result. Measured cost: 2,281 modules, 117 MB, 4-5 s per query. | S1 scans a 300-dependency ML app in under 10 minutes and under 2 GB |
| Cost | B's scope on A's calendar | S1 folds A2+A4+A5 into one evening, but A1a alone took a research day plus 48 commits, so expect 2-3 days. Cut Pipfile/pylock, SARIF, the Action, the blog and the ≤1 s/query target. Dogfood 4 apps, not 8. Label 25 advisories, not 50 (about 25 minutes per label). | S1 merges by the end of Day 2 |
| Evidence skeptic | A, gated on a half-day spike first | Nobody has measured how many real `path_proven` findings to expect. In the research, flaskr's 2 reachable probes out of 6 hit functions with no advisory, and FastAPI reached 0 of 6. G3 gates only the synthetic E1 case, and a precision of 2/2 has a Wilson lower bound of about 0.34. The headline overclaims, because the changed set contains other advisories' fixes. The labels are not blind. | The spike finds 5 or more real paths without A7 |
| Maintainer | A, as one product with explicit freezes | Archive the service (FastAPI, Postgres, LangGraph, web) on a branch and ship a CLI-only 1.0. Remove or pin `packaging._parser` (`inventory.py:19`). The semver promise covers JSON `schema_version` and CLI flags only. The dataset gets its own DOI and is upstreamed. Not D, because it breaks the L3 freeze. | A named co-maintainer for the service |

## Round 2 (anonymous peer review; every advisor partially conceded)

- **Cost** conceded to the skeptic: run the spike on Day 1. If it finds 3 or more paths, ship B's scope on A's calendar; otherwise pivot to C.
- **Reliability** conceded to cost: B's scope on A's calendar, with its hardening items funded by the freed days. It held on dogfood: at least 4 apps over two days, including one ML app and one app with no lockfile.
- **Maintainer** conceded to cost: B's scope on A's calendar plus its freezes. Spare days absorb S1's overrun.
- **Evidence skeptic** conceded to cost on labels: 25, with careful blinding. It held on the spike: run it first, and switch to C if it finds fewer than 5 paths.
- **Security** conceded to cost: SARIF and the Action move to 1.0.1. Three items are non-negotiable in 1.0: S1 archive and hash safety, `db update` caps, and terminal control-character stripping.

## Tradeoffs

| Option | Main upside | Main cost | Biggest risk |
|---|---|---|---|
| A, as drafted | The most complete launch (Action, SARIF, 50 labels, 8 apps) | S1 is underestimated by 1-2 days; labelling is not blind | Slips past Dec 1, or launches a wall of `unknown` |
| B, lean 7 days | Fastest finish | No buffer for S1's overrun | Finishes on time but untested on real apps |
| C, dataset first | The most defensible contribution | The scanner barely ships | Low adoption; few people use datasets |
| D, A + A7 | More `path_proven` findings | Breaks the L3 freeze, adds about 2 days, risks new false paths | The project never closes |
| **A′ (converged)** | Spike-gated; B's scope on A's calendar; security and reliability core kept; CLI-only with the service archived | Defers SARIF, the Action and Pipfile/pylock to 1.0.1 | The spike result may force the C pivot on Day 1 |

## Verdict

**A′.**
1. Day 1 starts with a half-day pre-registered spike to measure the real `path_proven` yield.
2. Then build B's scope on A's 10-day calendar:
   - CLI-only 1.0, with the service archived;
   - security core in S1 and `db update`;
   - reliability must-adds;
   - 25 labels, frozen before any tool run on them;
   - dogfood on 4-5 apps, including one ML app and one with no lockfile.
3. SARIF, the Action, Pipfile/pylock and the arXiv paper move to 1.0.1 and later.
4. D is rejected.

**Confidence: medium.** The schedule estimate rests on measured velocity. The thing that decides whether the scanner is worth shipping is the `path_proven` yield, and nobody has measured it.

**Minority report** (skeptic and cost, in round 2): if real apps without A7 produce almost no `path_proven`, ten days of scanner work buys a ranked list of `unknown`s. The dataset is then the only thing worth launching. Pivot to C at once, and do not spend the week finding this out at dogfood.

**Kill criteria.**
- **Spike, Day 1.** Fewer than 5 hand-traced (app, advisory) pairs where app code reaches the advisory's true symbol through explicit calls. Then pivot to C.
- **S1, end of Day 3.** S1 not merged. Then cut dogfood to 3 apps and drop per-query performance work. Never cut the security core.
- **Dogfood, Day 8.** Any `path_proven` whose final callee is not a hand-checked true symbol. The headline becomes counts with the error stated, and the "precision" wording is removed.
