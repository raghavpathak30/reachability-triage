import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from reachability.index.discovery import EXCLUDED_DIR_NAMES
from reachability.scan.inventory import ScanError
from reachability.scan.models import Gap
from reachability.scan.wheel_extract import preparse_error
from reachability.triage.index_adapter import RepoIndex, build_repo_index

# First path components of a wheel that never become part of the combined root.
SKIPPED_WHEEL_DIRS = frozenset({"tests", "test", "testing", "docs", "doc", "examples", "benchmarks"})


class CombinedIndexError(ScanError):
    pass


def _is_excluded(name: str) -> bool:
    return name in EXCLUDED_DIR_NAMES or name.endswith(".egg-info")


@dataclass
class AppFile:
    source: Path  # real file under the scan root
    rel_in_root: str  # path inside its import root (and inside the combined root)
    display: str  # path relative to the app dir, for output


def find_app_files(app_dir: Path, gaps: list[Gap]) -> list[AppFile]:
    """Walk app_dir; assign each .py file to its deepest import root.

    An import root is app_dir itself, or a directory with no `__init__.py`, no packaged
    ancestor below app_dir, and at least one direct child package.
    """
    files: list[tuple[Path, Path]] = []  # (path, import root dir)

    def is_root(d: Path, packaged: bool) -> bool:
        if d == app_dir:
            return True
        if packaged or (d / "__init__.py").is_file():
            return False
        try:
            children = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return False
        return any(c.is_dir(follow_symlinks=False) and (Path(c.path) / "__init__.py").is_file() for c in children)

    def walk(d: Path, root: Path, packaged: bool) -> None:
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            p = Path(e.path)
            rel = p.relative_to(app_dir).as_posix()
            if e.is_symlink():
                gaps.append(Gap("skipped_symlink", None, f"app/{rel}"))
                continue
            if e.is_dir(follow_symlinks=False):
                if _is_excluded(e.name):
                    continue
                child_packaged = packaged or (d != app_dir and (d / "__init__.py").is_file()) or (
                    d == app_dir and (d / "__init__.py").is_file()
                )
                new_root = p if is_root(p, child_packaged) else root
                walk(p, new_root, child_packaged)
            elif e.is_file(follow_symlinks=False) and e.name.endswith(".py"):
                files.append((p, root))

    walk(app_dir, app_dir, False)
    return [
        AppFile(p, p.relative_to(root).as_posix(), p.relative_to(app_dir).as_posix()) for p, root in files
    ]


@dataclass
class CombinedIndex:
    root: Path
    index: RepoIndex
    origin: dict[str, str]  # combined-relative posix path -> "app" or dist name
    display: dict[str, str]  # combined-relative posix path -> output path
    gaps: list[Gap] = field(default_factory=list)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def rel(self, path: Path) -> str:
        return Path(path).relative_to(self.root).as_posix()

    def origin_of(self, path: Path) -> str:
        return self.origin.get(self.rel(path), "?")

    def display_path(self, path: Path) -> str:
        try:
            return self.display[self.rel(path)]
        except (ValueError, KeyError):
            return Path(path).name

    @property
    def app_modules(self) -> set[str]:
        return {m.dotted_name for m in self.index.report.modules if self.origin_of(m.file_path) == "app"}

    def module_origins(self) -> dict[str, str]:
        return {m.dotted_name: self.origin_of(m.file_path) for m in self.index.report.modules}


def _make_root(tmp_parent: Path | None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="rscan-", dir=tmp_parent)).resolve()
    if any(part in ("test", "tests") for part in root.parts):
        shutil.rmtree(root, ignore_errors=True)
        raise CombinedIndexError(
            "combined index root path contains a 'test' or 'tests' component, which would mark every "
            "module as a test; set TMPDIR to a path without one"
        )
    return root


def _place(root: Path, rel: str, src: Path, origin: str, display: str, state: "CombinedIndex") -> None:
    dest = root / rel
    if rel in state.origin:
        state.gaps.append(
            Gap("collision", None, f"{display} collides with {state.display[rel]}; first kept")
        )
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    state.origin[rel] = origin
    state.display[rel] = display


def wheel_top_level_files(tree: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for p in sorted(tree.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(tree).as_posix()
        first = rel.split("/")[0]
        if first.endswith(".dist-info") or first.endswith(".data") or first in SKIPPED_WHEEL_DIRS:
            continue
        if not rel.endswith(".py"):
            continue
        out.append((rel, p))
    return out


def build_combined(
    app_dir: Path, dists: dict[str, Path], tmp_parent: Path | None = None
) -> CombinedIndex:
    """Copy app modules and each dist's top-level packages into one root and index it."""
    root = _make_root(tmp_parent)
    gaps: list[Gap] = []
    state = CombinedIndex(root, None, {}, {}, gaps)  # type: ignore[arg-type]
    try:
        for af in find_app_files(Path(app_dir), gaps):
            err = preparse_error(af.source)
            if err is not None:
                gaps.append(Gap("unparsed_file", None, f"app/{af.display}: {err}"))
                continue
            _place(root, af.rel_in_root, af.source, "app", f"app/{af.display}", state)
        for dist in sorted(dists):
            for rel, src in wheel_top_level_files(dists[dist]):
                _place(root, rel, src, dist, f"{dist}/{rel}", state)
        state.index = build_repo_index(root)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return state
