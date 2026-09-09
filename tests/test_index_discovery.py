from pathlib import Path

from reachability.index.discovery import discover_modules
from reachability.index.models import ModuleKind

from conftest import write_tree


def test_discovers_src_layout_modules(tmp_path):
    write_tree(tmp_path, {"src/pkg/mod.py": ""})

    modules = discover_modules(tmp_path)

    assert len(modules) == 1
    m = modules[0]
    assert m.dotted_name == "pkg.mod"
    assert m.file_path == tmp_path / "src" / "pkg" / "mod.py"
    assert m.kind == ModuleKind.MODULE


def test_discovers_package_with_init(tmp_path):
    write_tree(tmp_path, {"pkg/__init__.py": ""})

    modules = discover_modules(tmp_path)

    assert len(modules) == 1
    m = modules[0]
    assert m.dotted_name == "pkg"
    assert m.kind == ModuleKind.PACKAGE
    assert m.package == "pkg"


def test_discovers_namespace_package(tmp_path):
    write_tree(tmp_path, {"nspkg/sub/mod.py": ""})

    modules = discover_modules(tmp_path)

    assert len(modules) == 1
    m = modules[0]
    assert m.dotted_name == "nspkg.sub.mod"
    assert m.kind == ModuleKind.MODULE


def test_discovers_plain_top_level_module(tmp_path):
    write_tree(tmp_path, {"foo.py": ""})

    modules = discover_modules(tmp_path)

    assert len(modules) == 1
    m = modules[0]
    assert m.dotted_name == "foo"
    assert m.package == ""


def test_excludes_vendored_and_cache_dirs(tmp_path):
    write_tree(
        tmp_path,
        {
            ".venv/lib/somepkg/mod.py": "",
            "__pycache__/mod.py": "",
            "real.py": "",
        },
    )

    modules = discover_modules(tmp_path)

    dotted_names = {m.dotted_name for m in modules}
    assert dotted_names == {"real"}
