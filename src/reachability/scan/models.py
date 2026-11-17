from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Gap:
    kind: str  # no_wheel, unparsed_file, skipped_symlink, budget, collision, dist_error, excluded_dir
    dist: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "dist": self.dist, "detail": self.detail}


@dataclass(frozen=True)
class Target:
    node_id: str
    module: str
    qualname: str
    ordinal: int
    file: str
    lineno: int
    changed: str  # changed | added | removed
    confidence: str = "medium"
    named: bool = False

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.module, self.qualname, self.ordinal)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "file": self.file,
            "lineno": self.lineno,
            "confidence": self.confidence,
            "named": self.named,
            "changed": self.changed,
        }


@dataclass(frozen=True)
class PathEdge:
    file: str
    lineno: int
    caller: str
    callee: str

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "lineno": self.lineno, "caller": self.caller, "callee": self.callee}


@dataclass(frozen=True)
class Finding:
    advisory_id: str
    aliases: list[str]
    dist: str
    version: str
    fixed_in: str | None
    verdict: str  # reachable | reachable_only_from_tests | unknown
    evidence_level: str | None
    reason_code: str | None
    path: list[PathEdge] | None = None
    partial_path: list[PathEdge] | None = None
    targets: list[Target] = field(default_factory=list)
    targets_truncated: bool = False
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "advisory_id": self.advisory_id,
            "aliases": list(self.aliases),
            "dist": self.dist,
            "version": self.version,
            "fixed_in": self.fixed_in,
            "verdict": self.verdict,
            "evidence_level": self.evidence_level,
            "reason_code": self.reason_code,
            "path": None if self.path is None else [e.to_dict() for e in self.path],
            "partial_path": None if self.partial_path is None else [e.to_dict() for e in self.partial_path],
            "targets": [t.to_dict() for t in self.targets],
            "targets_truncated": self.targets_truncated,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class FetchedDist:
    dist: str
    version: str
    status: str  # fetched | no_wheel | budget
    wheel_filename: str | None = None
    sha256: str | None = None
    root: Path | None = None
    reason: str | None = None
    error: str | None = None  # exception class name when the fetch itself raised


@dataclass
class FetchResult:
    dists: dict[tuple[str, str], FetchedDist] = field(default_factory=dict)
    gaps: list[Gap] = field(default_factory=list)


@dataclass
class ScanResult:
    snapshot_date: str
    snapshot_sha256: str
    target_env: str
    app_root: str | None
    lead: str | None
    lockfile: str | None
    hint: str | None
    packages: list[dict[str, Any]]
    malicious: list[dict[str, Any]]
    findings: list[Finding]
    gaps: list[Gap]
    closure_complete: bool
    stats: dict[str, Any]
    timings: dict[str, float]
    notes: list[str] = field(default_factory=list)
