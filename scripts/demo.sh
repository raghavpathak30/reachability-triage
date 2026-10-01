#!/usr/bin/env bash
# Narrated demo: brings the compose stack up in STUB mode and triages three
# offline fixture packages over HTTP (reachable / not_reachable / unknown).
# No key, no prompts, no manual steps. Exits non-zero on any failed job,
# timeout, or unexpected verdict, so it cannot narrate success over a failure.
#
# Env: DEMO_PACE (multiplier on every narration pause; default 1, sized for
# ~2-3 minutes; DEMO_PACE=0 for a fast run), API_PORT (default: a free port),
# PYTHON (default python3), DEMO_TIMEOUT (per-job poll timeout, default 120s).
# DEMO_ALLOW_DIRTY=1 skips the clean-tree check (default: refuse on a dirty
# tree, because the demo builds the working tree). A local .env is ignored
# (an empty --env-file is passed to compose) so the demo is reproducible.
# Never sets DOCKER_HOST -- export it yourself if needed.
set -euo pipefail

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
if [ "${DEMO_ALLOW_DIRTY:-0}" != 1 ] && [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
  echo "FAIL: working tree is dirty; the demo builds the working tree. Commit first or set DEMO_ALLOW_DIRTY=1." >&2
  exit 2
fi
PYTHON="${PYTHON:-python3}"
DEMO_PACE="${DEMO_PACE:-1}"
DEMO_TIMEOUT="${DEMO_TIMEOUT:-120}"
TMP="$(mktemp -d)"
# Empty env file: stops compose from picking up a developer's local .env.
: > "$TMP/empty.env"
PROJECT="reach-demo-$$"
COMPOSE=(docker compose --env-file "$TMP/empty.env" -p "$PROJECT" -f docker-compose.yml -f docker-compose.smoke.yml)
STARTED=0

say() { # say "text" [seconds-at-pace-1]
  echo "$1"
  sleep "$(awk -v a="${2:-4}" -v p="$DEMO_PACE" 'BEGIN{print a*p}')"
}

cleanup() {
  local rc=$?
  set +e
  if [ "$STARTED" = 1 ]; then
    if [ "$rc" -ne 0 ]; then
      (cd "$ROOT" && "${COMPOSE[@]}" logs --no-color --tail 40 >&2)
    fi
    (cd "$ROOT" && "${COMPOSE[@]}" down -v --rmi local >/dev/null 2>&1)
  fi
  rm -rf "$TMP"
  exit "$rc"
}
trap cleanup EXIT

cd "$ROOT"

say "== reachability-triage demo ==" 3
say "Question: a dependency has a CVE in one function. Can that function actually be reached from the package's entrypoints?" 8
say "MODE: this demo runs in STUB mode. The LLM client is a deterministic policy stub, NOT a real model." 8
say "What is real in both modes: the static index and call-graph analysis (build_repo_index, compute_reachability, tool dispatch). The verdict comes from that analysis, not from model prose." 10
say "Every verdict below is printed next to its llm_mode so a stub verdict can never pass for a real-model one." 6

say "-- starting the stack (Postgres, migrations, API, worker) --" 3
"$PYTHON" scripts/build_fixture_wheels.py --out "$TMP/wheels" >/dev/null
export FIXTURE_WHEELS_DIR="$TMP/wheels"
if [ -z "${API_PORT:-}" ]; then
  API_PORT="$("$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
fi
export API_PORT
BASE="http://127.0.0.1:${API_PORT}"

STARTED=1
if ! "${COMPOSE[@]}" up -d --build --wait >"$TMP/up.log" 2>&1; then
  tail -30 "$TMP/up.log" >&2
  echo "FAIL: docker compose up did not reach healthy" >&2
  exit 1
fi
MODE="$("${COMPOSE[@]}" exec -T worker printenv TRIAGE_LLM_MODE)"
if [ "$MODE" != "stub" ]; then
  echo "FAIL: worker TRIAGE_LLM_MODE is '$MODE', expected 'stub'" >&2
  exit 1
fi
say "Stack is up. Worker TRIAGE_LLM_MODE=$MODE. The fixture packages are offline wheels, so no network is used." 6

run_case() { # run_case <case> <expected-verdict>
  local case="$1" expected="$2" result
  result="$("$PYTHON" scripts/triage_client.py run --base "$BASE" \
    --cases "$TMP/wheels/cases.json" --case "$case" --timeout "$DEMO_TIMEOUT")"
  "$PYTHON" scripts/triage_client.py check --cases "$TMP/wheels/cases.json" \
    --case "$case" --result "$result" --expect "$expected" >/dev/null
  "$PYTHON" scripts/triage_client.py show --result "$result"
}

say "" 1
say "-- case 1: reachable (fixture 02, a three-hop chain from a __main__ entrypoint to pkg.sink:vulnerable) --" 6
run_case reachable reachable
say "A reachable verdict must carry evidence: the call path above, entrypoint to sink. This is the case that is worth a human's time." 8

say "" 1
say "-- case 2: not_reachable (fixture 09, the vulnerable module is never imported) --" 6
run_case not_reachable not_reachable
say "not_reachable carries its reason: no call path from any entrypoint. This is the noise a version-matching scanner cannot filter out." 8

say "" 1
say "-- case 3: unknown (fixture 13, getattr(module, name)() with no literal name) --" 6
run_case unknown unknown
say "The call site is dynamic, so the analysis cannot rule the function out. It answers unknown instead of guessing." 8
say "Why unknown is the safe default: a wrong confident not_reachable would tell someone to ignore a live vulnerability. That is the worst outcome, so ambiguity resolves toward unknown, and so does every LLM failure (timeout, refusal, budget exhaustion)." 12

say "" 1
say "-- what this demo does not show --" 3
say "Stub mode only. Real-model injection resistance is unclaimed: no real-model adversarial run has completed an investigation (0 of 3 completed; budget exhaustion is not counted as resistance)." 10
say "Local only: no auth, no deployment. Tearing the stack down." 4

echo "DEMO OK (llm_mode=stub)"
