from pathlib import Path

from .models import ModuleKind, ModuleRecord

EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    "build",
    "dist",
}


def _is_excluded_dir(name: str) -> bool:
    return name in EXCLUDED_DIR_NAMES or name.endswith(".egg-info")


def _is_under_excluded_dir(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return any(_is_excluded_dir(part) for part in rel.parts[:-1])


def discover_modules(repo_root: Path) -> list[ModuleRecord]:
    src_dir = repo_root / "src"
    root = src_dir if src_dir.is_dir() else repo_root

    modules: list[ModuleRecord] = []
    for py_file in root.rglob("*.py"):
        if _is_under_excluded_dir(py_file, root):
            continue

        rel = py_file.relative_to(root)
        parts = list(rel.parts)

        if parts[-1] == "__init__.py":
            dotted_parts = parts[:-1]
            dotted_name = ".".join(dotted_parts)
            kind = ModuleKind.PACKAGE
            package = dotted_name
        else:
            dotted_parts = parts[:-1] + [parts[-1][: -len(".py")]]
            dotted_name = ".".join(dotted_parts)
            kind = ModuleKind.MODULE
            package = ".".join(dotted_parts[:-1])

        modules.append(
            ModuleRecord(
                dotted_name=dotted_name,
                file_path=py_file,
                kind=kind,
                package=package,
            )
        )

    return modules
