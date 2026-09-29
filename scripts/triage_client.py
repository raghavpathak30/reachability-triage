#!/usr/bin/env python3
"""Tiny stdlib HTTP client shared by scripts/smoke_compose.sh and
scripts/demo.sh (no jq/curl dependency).

  run    submit one case from cases.json, poll to a terminal state, print the
         result as one JSON line. Exit 0 only for `completed`.
           1 = job `failed` or unexpected status, 2 = poll timeout,
           3 = POST did not return HTTP 202 with a UUID id.
  check  assert a `run` result against the case's label-derived allowed set
         (and llm_mode == stub, and a non-empty path when the label allows
         only `reachable`). Prints `PASS <case> <verdict> llm_mode=<mode>`;
         exits 1 with a message otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid


def _load_case(cases_path: str, name: str) -> dict:
    for case in json.load(open(cases_path)):
        if case["case"] == name:
            return case
    raise SystemExit(f"unknown case {name!r} in {cases_path}")


def run(args: argparse.Namespace) -> int:
    case = _load_case(args.cases, args.case)
    body = json.dumps(
        {
            "package": case["package"],
            "version": case["version"],
            "target_module": case["target_module"],
            "target_symbol": case["target_symbol"],
        }
    ).encode()
    req = urllib.request.Request(
        f"{args.base}/v1/triage", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_code, payload = resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        print(f"FAIL {args.case}: POST /v1/triage returned HTTP {exc.code}", file=sys.stderr)
        return 3
    if status_code != 202:
        print(f"FAIL {args.case}: POST /v1/triage returned HTTP {status_code}, want 202", file=sys.stderr)
        return 3
    try:
        job_id = str(uuid.UUID(payload["id"]))
    except (KeyError, ValueError):
        print(f"FAIL {args.case}: POST response has no parseable UUID id: {payload!r}", file=sys.stderr)
        return 3

    deadline = time.monotonic() + args.timeout
    last = payload.get("status")
    while True:
        with urllib.request.urlopen(f"{args.base}/v1/triage/{job_id}", timeout=10) as resp:
            record = json.load(resp)
        last = record["status"]
        if last == "completed":
            break
        if last == "failed":
            print(f"FAIL {args.case}: job {job_id} status=failed error={record.get('error')}", file=sys.stderr)
            return 1
        if last not in ("queued", "running"):
            print(f"FAIL {args.case}: job {job_id} unexpected status {last!r}", file=sys.stderr)
            return 1
        if time.monotonic() >= deadline:
            print(
                f"FAIL {args.case}: timed out after {args.timeout}s waiting for job {job_id}; last status={last}",
                file=sys.stderr,
            )
            return 2
        time.sleep(args.interval)

    result = (record.get("finding") or {}).get("result") or {}
    path = result.get("path")
    print(
        json.dumps(
            {
                "case": args.case,
                "id": job_id,
                "status": record["status"],
                "llm_mode": record.get("llm_mode"),
                "verdict": result.get("verdict"),
                "path_len": len(path) if path else 0,
                "reason": result.get("reason"),
            }
        )
    )
    return 0


def check(args: argparse.Namespace) -> int:
    case = _load_case(args.cases, args.case)
    result = json.loads(args.result)
    name = case["case"]
    if result.get("status") != "completed":
        print(f"FAIL {name}: status {result.get('status')!r} is not completed", file=sys.stderr)
        return 1
    verdict = result.get("verdict")
    if not verdict:
        print(f"FAIL {name}: missing or null finding.result.verdict", file=sys.stderr)
        return 1
    allowed = case["allowed_verdicts"]
    if verdict not in allowed:
        print(
            f"FAIL {name}: verdict {verdict!r} not in label allowed set {allowed}",
            file=sys.stderr,
        )
        return 1
    if result.get("llm_mode") != "stub":
        print(f"FAIL {name}: llm_mode {result.get('llm_mode')!r} != 'stub'", file=sys.stderr)
        return 1
    if allowed == ["reachable"] and result.get("path_len", 0) < 1:
        print(f"FAIL {name}: reachable verdict carries no path evidence", file=sys.stderr)
        return 1
    print(f"PASS {name} {verdict} llm_mode={result['llm_mode']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--base", required=True)
    p_run.add_argument("--cases", required=True)
    p_run.add_argument("--case", required=True)
    p_run.add_argument("--timeout", type=float, default=120)
    p_run.add_argument("--interval", type=float, default=1.0)
    p_chk = sub.add_parser("check")
    p_chk.add_argument("--cases", required=True)
    p_chk.add_argument("--case", required=True)
    p_chk.add_argument("--result", required=True)
    args = parser.parse_args()
    return run(args) if args.cmd == "run" else check(args)


if __name__ == "__main__":
    sys.exit(main())
