import json
from typing import Any

from reachability.scan.models import ScanResult
from reachability.scan.reasons import ReasonCode
from reachability.scan.textsafe import strip_control, strip_control_line

SCHEMA_VERSION = "0"
CLOSURE_LINE = (
    "Import closure was not evaluated: only path_proven findings carry an evidence level; "
    "every unknown finding has none."
)


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return strip_control(value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {_clean(k): _clean(v) for k, v in value.items()}
    return value


def to_json_dict(result: ScanResult) -> dict[str, Any]:
    verdicts = {"reachable": 0, "reachable_only_from_tests": 0, "unknown": 0}
    reasons: dict[str, int] = {}
    evidence: dict[str, int] = {}
    for f in result.findings:
        verdicts[f.verdict] += 1
        if f.reason_code:
            reasons[f.reason_code] = reasons.get(f.reason_code, 0) + 1
        if f.evidence_level:
            evidence[f.evidence_level] = evidence.get(f.evidence_level, 0) + 1
    data = {
        "schema_version": SCHEMA_VERSION,
        "snapshot": {"date": result.snapshot_date, "sha256": result.snapshot_sha256},
        "target_env": result.target_env,
        "app_root": result.app_root,
        "import_closure_evaluated": False,
        "closure_complete": result.closure_complete,
        "lead": result.lead,
        "inventory": {"lockfile": result.lockfile, "hint": result.hint, "packages": result.packages},
        "malicious": result.malicious,
        "summary": {"verdicts": verdicts, "reasons": dict(sorted(reasons.items())), "evidence": evidence},
        "findings": [f.to_dict() for f in result.findings],
        "gaps": [g.to_dict() for g in result.gaps],
        "stats": result.stats,
        "timings": result.timings,
    }
    return _clean(data)


def render_json(result: ScanResult) -> str:
    return json.dumps(to_json_dict(result), indent=2, sort_keys=False) + "\n"


def render_text(result: ScanResult) -> str:
    d = to_json_dict(result)
    out: list[str] = []
    if d["lead"]:
        out.append(d["lead"])
    s = d["summary"]["verdicts"]
    out.append(
        f"{len(d['findings'])} advisories matched on pinned packages: "
        f"{s['reachable']} reachable, {s['reachable_only_from_tests']} reachable only from tests, "
        f"{s['unknown']} unknown"
    )
    if d["malicious"]:
        out.append("malicious packages:")
        for m in d["malicious"]:
            out.append(f"  {m['advisory_id']} {m['dist']} {m['version']} ({m['signal']})")
    for f in d["findings"]:
        if f["evidence_level"] != "path_proven":
            continue
        out.append(f"{f['advisory_id']} {f['dist']} {f['version']}: {f['verdict']} (path_proven)")
        for e in f["path"]:
            out.append(f"  {e['file']}:{e['lineno']} {e['caller']} -> {e['callee']}")
        out.append(f"  fix: upgrade {f['dist']} {f['version']} -> {f['fixed_in']}")
    unknown = [f for f in d["findings"] if f["verdict"] == "unknown"]
    out.append(f"unknown: {len(unknown)}")
    counts: dict[str, int] = {}
    for f in unknown:
        counts[f["reason_code"]] = counts.get(f["reason_code"], 0) + 1
    for code in sorted(counts):
        out.append(f"  {code}: {counts[code]}")
    cut = [f for f in unknown if f["reason_code"] == ReasonCode.SCAN_BUDGET.value]
    if cut:
        out.append("analysis budget reached; not analysed: " + ", ".join(f["advisory_id"] for f in cut))
    budget_gaps = [g for g in d["gaps"] if g["kind"] == "budget"]
    if budget_gaps:
        out.append("fetch budget reached; not fetched: " + ", ".join(str(g["dist"]) for g in budget_gaps))
    if d["gaps"]:
        out.append(f"gaps: {len(d['gaps'])} (closure incomplete; see --format json)")
    out.append(CLOSURE_LINE)
    return "\n".join(strip_control_line(line) for line in out) + "\n"

