#!/usr/bin/env bash
# Compose smoke gate. Tests COMMITTED content only: it clones the repo's HEAD
# into a temp dir and runs everything from the clone, so it refuses to run on
# a dirty tree (commit first). Never sets DOCKER_HOST -- export it yourself if
# your default daemon is not the one you want.
#
# Env: SMOKE_TIMEOUT (per-job poll timeout, default 120s), API_PORT and
# WEB_PORT (default: distinct free ports), PYTHON (default python3), TRIAGE_LLM_MODE (default: stub).
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
if [ -z "${WEB_PORT:-}" ]; then
  WEB_PORT="$API_PORT"
  while [ "$WEB_PORT" = "$API_PORT" ]; do
    WEB_PORT="$("$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
  done
fi
export WEB_PORT
BASE="http://127.0.0.1:${API_PORT}"
WEB_BASE="http://127.0.0.1:${WEB_PORT}"

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

# (h)-(j) web container: SPA served, no inline script/style, security headers
# on 200 and on genuine 404s (nginx's own and the proxied api's).
"$PYTHON" - "$WEB_BASE" <<'PY'
import json, re, sys, urllib.error, urllib.request

base = sys.argv[1]
CSP = "default-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"


def fetch(path):
    try:
        r = urllib.request.urlopen(base + path, timeout=10)
    except urllib.error.HTTPError as e:
        r = e
    return r.status if hasattr(r, "status") else r.code, r.headers, r.read().decode()


def fail(msg):
    print("FAIL: " + msg, file=sys.stderr)
    sys.exit(1)


def headers_ok(label, h):
    if h.get_all("Content-Security-Policy") != [CSP]:
        fail(f"{label}: Content-Security-Policy is {h.get_all('Content-Security-Policy')!r}")
    if h.get_all("X-Content-Type-Options") != ["nosniff"]:
        fail(f"{label}: X-Content-Type-Options is {h.get_all('X-Content-Type-Options')!r}")
    if h.get_all("Referrer-Policy") != ["no-referrer"]:
        fail(f"{label}: Referrer-Policy is {h.get_all('Referrer-Policy')!r}")


status, h, body = fetch("/")
if status != 200 or '<div id="root">' not in body:
    fail(f"(h) GET / status {status} or missing root div")
headers_ok("(i) /", h)
for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", body, re.S | re.I):
    if "src=" not in m.group(1) or m.group(2).strip():
        fail("(i) inline <script> in served /")
if re.search(r"<style\b|\sstyle=", body, re.I):
    fail("(i) inline <style> or style= in served /")

status, h, _ = fetch("/assets/does-not-exist.js")
if status != 404:
    fail(f"(j) /assets/does-not-exist.js status {status}, want 404")
headers_ok("(j) /assets 404", h)

status, h, body = fetch("/v1/triage/not-a-uuid")
if status != 404:
    fail(f"(j) proxied /v1/triage/not-a-uuid status {status}, want 404")
try:
    code = json.loads(body)["error"]["code"]
except (ValueError, KeyError, TypeError):
    fail(f"(j) proxied 404 body is not the api error envelope: {body[:200]!r}")
if code != "NOT_FOUND":
    fail(f"(j) proxied 404 error code {code!r}")
headers_ok("(j) proxied 404", h)
PY

# (k) one case through the web proxy, plus Location passthrough on POST.
"$PYTHON" - "$WEB_BASE" "$TMP/wheels/cases.json" <<'PY'
import json, sys, urllib.request

base, cases = sys.argv[1], sys.argv[2]
case = next(c for c in json.load(open(cases)) if c["case"] == "reachable")
body = json.dumps({k: case[k] for k in ("package", "version", "target_module", "target_symbol")}).encode()
req = urllib.request.Request(base + "/v1/triage", data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as r:
    job_id = json.load(r)["id"]
    loc = r.headers.get("Location")
if loc != f"/v1/triage/{job_id}":
    print(f"FAIL: (k) Location is {loc!r}, want /v1/triage/{job_id}", file=sys.stderr)
    sys.exit(1)
PY
RESULT="$("$PYTHON" scripts/triage_client.py run --base "$WEB_BASE" \
  --cases "$TMP/wheels/cases.json" --case reachable --timeout "$SMOKE_TIMEOUT")"
"$PYTHON" scripts/triage_client.py check \
  --cases "$TMP/wheels/cases.json" --case reachable --result "$RESULT"

echo "SMOKE OK"
