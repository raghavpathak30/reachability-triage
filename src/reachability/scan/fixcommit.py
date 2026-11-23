"""Fix-commit lookup: which functions did an advisory's own fix commit change.

Commit URLs come only from the snapshot record's references. Diff text and wheel source are
parsed as text and with `ast`; nothing is imported or run.
"""

import re
from dataclasses import dataclass

from reachability.scan.advisories import Record

OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
SHA_RE = re.compile(r"[0-9a-fA-F]{7,40}")
_COMMIT_URL = re.compile(
    r"https://github\.com/([^/\s?#]+)/([^/\s?#]+)/commit/([0-9a-f]{7,40})/?", re.IGNORECASE
)
_PACKAGE_URL = re.compile(r"https://github\.com/([^/\s?#]+)/([^/\s?#]+?)(?:\.git)?/?", re.IGNORECASE)


@dataclass(frozen=True)
class Commit:
    owner: str
    repo: str
    sha: str

    def label(self) -> str:
        return f"{self.owner}/{self.repo}@{self.sha}"


def valid_parts(owner: str, repo: str, sha: str) -> bool:
    return bool(
        OWNER_RE.fullmatch(owner)
        and REPO_RE.fullmatch(repo)
        and not repo.startswith(".")
        and SHA_RE.fullmatch(sha)
    )


def package_repo(record: Record) -> tuple[str, str] | None:
    for rtype, url in record.references:
        if rtype == "PACKAGE":
            m = _PACKAGE_URL.fullmatch(url)
            if m:
                return m.group(1).lower(), m.group(2).lower()
    return None


def usable_commits(records: list[Record]) -> list[Commit]:
    """Same-repo GitHub commit URLs (any reference type), union over the records, in sorted order."""
    found: dict[tuple[str, str, str], Commit] = {}
    for rec in records:
        repo = package_repo(rec)
        if repo is None:
            continue
        for _rtype, url in rec.references:
            m = _COMMIT_URL.fullmatch(url)
            if not m:
                continue
            owner, name, sha = m.group(1), m.group(2), m.group(3).lower()
            if (owner.lower(), name.lower()) != repo or not valid_parts(owner, name, sha):
                continue
            found.setdefault((owner.lower(), name.lower(), sha), Commit(owner.lower(), name.lower(), sha))
    return [found[k] for k in sorted(found)]
