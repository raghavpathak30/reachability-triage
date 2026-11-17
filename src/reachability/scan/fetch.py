import hashlib
import re
import shutil
import tempfile
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from packaging.utils import canonicalize_name

from reachability.scan.lock_wheels import PYPI_REGISTRY, LockEntry
from reachability.scan.models import FetchedDist, FetchResult, Gap
from reachability.scan.wheel_extract import extract_wheel
from reachability.scan.wheel_select import Candidate, choose_wheel

PYPI_HOST = "pypi.org"
FILES_HOST = "files.pythonhosted.org"


class Fetcher(Protocol):
    def get_json(self, url: str) -> dict: ...

    def download(self, url: str, dest: Path, *, max_bytes: int, deadline_seconds: float) -> None: ...


class LocalDirFetcher:
    """Serves wheels from a directory as a PyPI-shaped source (offline use and tests).

    Each wheel needs a `<wheel>.sha256` sidecar; the same selection, verification and
    extraction code then runs as for a download.
    """

    def __init__(self, wheel_dir: Path):
        self.wheel_dir = Path(wheel_dir)

    def get_json(self, url: str) -> dict:
        m = re.fullmatch(r"https://pypi\.org/pypi/([^/]+)/([^/]+)/json", url)
        if not m:
            raise ValueError(f"unexpected metadata url: {url}")
        name, version = m.group(1), m.group(2)
        urls = []
        for whl in sorted(self.wheel_dir.glob("*.whl")):
            parts = whl.name[: -len(".whl")].split("-")
            if len(parts) < 5:
                continue
            if canonicalize_name(parts[0]) != name or parts[1] != version:
                continue
            side = whl.with_name(whl.name + ".sha256")
            digest = side.read_text().strip() if side.is_file() else ""
            urls.append(
                {
                    "filename": whl.name,
                    "url": f"https://{FILES_HOST}/local/{whl.name}",
                    "packagetype": "bdist_wheel",
                    "digests": {"sha256": digest} if digest else {},
                }
            )
        return {"urls": urls}

    def download(self, url: str, dest: Path, *, max_bytes: int, deadline_seconds: float) -> None:
        shutil.copyfile(self.wheel_dir / Path(urlsplit(url).path).name, dest)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _no_wheel(dist: str, version: str, reason: str) -> tuple[FetchedDist, Gap]:
    return (
        FetchedDist(dist, version, "no_wheel", reason=reason),
        Gap("no_wheel", dist, f"{dist}=={version}: {reason}"),
    )


def _candidates(meta: dict) -> list[Candidate]:
    out = []
    for entry in meta.get("urls", []):
        if entry.get("packagetype") != "bdist_wheel" or not entry.get("filename"):
            continue
        out.append(Candidate(entry["filename"], entry["url"], (entry.get("digests") or {}).get("sha256") or None))
    return out


def _select(
    dist: str, version: str, fetcher: Fetcher, lock: LockEntry | None, req_hashes: tuple[str, ...]
) -> Candidate | str:
    """Return the chosen candidate, or a no_wheel reason string."""
    if lock is not None and lock.registry and lock.registry.rstrip("/") != PYPI_REGISTRY:
        if not lock.wheels or any(urlsplit(w.url).hostname != FILES_HOST for w in lock.wheels):
            return f"package comes from a non-PyPI registry ({urlsplit(lock.registry).hostname})"
    if lock is not None and lock.wheels:
        chosen = choose_wheel(list(lock.wheels))
        if chosen is None:
            return "no wheel in the lockfile matches the target environment"
        return chosen
    if lock is not None and lock.has_sdist:
        return "lockfile lists only an sdist"
    meta = fetcher.get_json(f"https://{PYPI_HOST}/pypi/{canonicalize_name(dist)}/{version}/json")
    cands = _candidates(meta)
    if req_hashes:
        allowed = {h.split(":", 1)[-1].lower() for h in req_hashes}
        cands = [c for c in cands if c.sha256 in allowed]
    chosen = choose_wheel(cands)
    if chosen is None:
        return "no suitable wheel"
    return chosen


def fetch_dist(
    dist: str,
    version: str,
    fetcher: Fetcher,
    store: Path,
    *,
    lock: LockEntry | None = None,
    req_hashes: tuple[str, ...] = (),
) -> tuple[FetchedDist, list[Gap]]:
    name = canonicalize_name(dist)
    chosen = _select(dist, version, fetcher, lock, req_hashes)
    if isinstance(chosen, str):
        fd, gap = _no_wheel(dist, version, chosen)
        return fd, [gap]
    expected = chosen.sha256
    if not expected:
        fd, gap = _no_wheel(dist, version, "no digest")
        return fd, [gap]
    tree = store / f"{name}-{version}-{expected}" / "tree"
    if not tree.is_dir():
        store.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=store) as tmp:
            wheel = Path(tmp) / "w.whl"
            fetcher.download(chosen.url, wheel, max_bytes=100 * 1024 * 1024, deadline_seconds=120.0)
            actual = _sha256_file(wheel)
            if actual != expected:
                fd, gap = _no_wheel(dist, version, "sha256 mismatch")
                return fd, [gap]
            staging = Path(tmp) / "tree"
            extract_wheel(wheel, staging)
            tree.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(tree)
    return FetchedDist(dist, version, "fetched", chosen.filename, expected, tree), []


def fetch_dists(
    pairs: list[tuple[str, str]],
    fetcher: Fetcher,
    store: Path,
    *,
    locks: dict[tuple[str, str], LockEntry] | None = None,
    req_hashes: dict[tuple[str, str], tuple[str, ...]] | None = None,
) -> FetchResult:
    store.mkdir(parents=True, exist_ok=True)
    result = FetchResult()
    for dist, version in pairs:
        key = (canonicalize_name(dist), version)
        fd, gaps = fetch_dist(
            dist, version, fetcher, store,
            lock=(locks or {}).get(key), req_hashes=(req_hashes or {}).get(key, ()),
        )
        result.dists[(dist, version)] = fd
        result.gaps.extend(gaps)
    return result
