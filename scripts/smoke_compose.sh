#!/usr/bin/env bash
# Compose smoke gate. Tests COMMITTED content only: it clones the repo's HEAD
# into a temp dir and runs everything from the clone, so it refuses to run on
# a dirty tree (commit first). Never sets DOCKER_HOST -- export it yourself if
# your default daemon is not the one you want.
#
# Env: SMOKE_TIMEOUT (per-job poll timeout, default 120s), API_PORT (default:
# a free port), PYTHON (default python3), TRIAGE_LLM_MODE (default: stub).
set -euo pipefail

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
PYTHON="${PYTHON:-python3}"
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-120}"

if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
  echo "FAIL: working tree is dirty; smoke tests the committed HEAD only. Commit first." >&2
  exit 2
fi

TMP="$(mktemp -d)"
PROJECT="reach-smoke-$$"
COMPOSE=(docker compose -p "$PROJECT" -f docker-compose.yml -f docker-compose.smoke.yml)
STARTED=0

cleanup() {
  local rc=$?
  set +e
  if [ "$STARTED" = 1 ]; then
    if [ "$rc" -ne 0 ]; then
      (cd "$TMP/clone" && "${COMPOSE[@]}" logs --no-color --tail 60 >&2)
    fi
    (cd "$TMP/clone" && "${COMPOSE[@]}" down -v --rmi local >/dev/null 2>&1)
  fi
  rm -rf "$TMP"
  exit "$rc"
}
trap cleanup EXIT

git clone -q --no-hardlinks "$ROOT" "$TMP/clone"
echo "smoke: testing clone HEAD $(git -C "$TMP/clone" rev-parse --short HEAD)"
cd "$TMP/clone"

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

# (a) the stub path is proven to be in use.
MODE="$("${COMPOSE[@]}" exec -T worker printenv TRIAGE_LLM_MODE)"
if [ "$MODE" != "stub" ]; then
  echo "FAIL: worker TRIAGE_LLM_MODE is '$MODE', expected 'stub'" >&2
  exit 1
fi

# (b) health
HTTP="$("$PYTHON" -c "import urllib.request,sys; print(urllib.request.urlopen(sys.argv[1], timeout=5).status)" "$BASE/healthz")"
if [ "$HTTP" != "200" ]; then
  echo "FAIL: /healthz returned $HTTP" >&2
  exit 1
fi

# (c)-(g) per case: submit (202), poll to completed, verdict/llm_mode/path checks.
for CASE in reachable not_reachable unknown; do
  RESULT="$("$PYTHON" scripts/triage_client.py run --base "$BASE" \
    --cases "$TMP/wheels/cases.json" --case "$CASE" --timeout "$SMOKE_TIMEOUT")"
  "$PYTHON" scripts/triage_client.py check \
    --cases "$TMP/wheels/cases.json" --case "$CASE" --result "$RESULT"
done

echo "SMOKE OK"
