from pathlib import Path


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel_path, content in files.items():
        p = root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root
