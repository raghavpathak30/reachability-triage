import ast

from reachability.index.discovery import discover_modules
from reachability.index.imports import build_import_table
from reachability.index.models import ModuleKind, ModuleRecord, TargetKind

from conftest import write_tree


def _table_for_source(tmp_path, source, *, module_path="mod.py", first_party_roots=None):
    write_tree(tmp_path, {module_path: source})
    modules = discover_modules(tmp_path)
    module = next(m for m in modules if m.file_path == tmp_path / module_path)
    tree = ast.parse(source)
    roots = first_party_roots if first_party_roots is not None else {m.dotted_name.split(".")[0] for m in modules}
    return build_import_table(module, tree, roots)


def test_two_level_relative_import_resolves(tmp_path):
    write_tree(
        tmp_path,
        {
            "a/__init__.py": "",
            "a/b/__init__.py": "",
            "a/b/c.py": "from .. import x\n",
        },
    )
    modules = discover_modules(tmp_path)
    module = next(m for m in modules if m.dotted_name == "a.b.c")
    roots = {m.dotted_name.split(".")[0] for m in modules}
    tree = ast.parse((tmp_path / "a/b/c.py").read_text())

    table = build_import_table(module, tree, roots)

    assert len(table.aliases) == 1
    alias = table.aliases[0]
    assert alias.resolved_absolute == "a"
    assert alias.target_symbol == "x"
    assert alias.is_relative is True


def test_import_as_alias_third_party(tmp_path):
    table = _table_for_source(tmp_path, "import numpy as np\n")

    assert len(table.aliases) == 1
    alias = table.aliases[0]
    assert alias.local_name == "np"
    assert alias.target_module == "numpy"
    assert alias.target_symbol is None
    assert alias.is_relative is False
    assert alias.resolved_absolute == "numpy"
    assert alias.target_kind == TargetKind.THIRD_PARTY


def test_from_import_as_alias_first_party(tmp_path):
    write_tree(
        tmp_path,
        {
            "a/__init__.py": "",
            "a/b.py": "",
            "importer.py": "from a.b import c as d\n",
        },
    )
    modules = discover_modules(tmp_path)
    module = next(m for m in modules if m.dotted_name == "importer")
    roots = {m.dotted_name.split(".")[0] for m in modules}
    tree = ast.parse((tmp_path / "importer.py").read_text())

    table = build_import_table(module, tree, roots)

    assert len(table.aliases) == 1
    alias = table.aliases[0]
    assert alias.local_name == "d"
    assert alias.target_module == "a.b"
    assert alias.target_symbol == "c"
    assert alias.is_relative is False
    assert alias.resolved_absolute == "a.b"
    assert alias.target_kind == TargetKind.FIRST_PARTY


def test_star_import_recorded_unresolved_not_expanded(tmp_path):
    table = _table_for_source(tmp_path, "from a.b import *\n")

    assert len(table.aliases) == 0
    assert len(table.star_imports) == 1


def test_stdlib_import_classified(tmp_path):
    table = _table_for_source(tmp_path, "import os\nimport sys\n")

    assert len(table.aliases) == 2
    assert all(a.target_kind == TargetKind.STDLIB for a in table.aliases)


def test_dotted_import_without_alias_binds_top_level_name(tmp_path):
    table = _table_for_source(tmp_path, "import a.b.c\n", first_party_roots={"a"})

    assert len(table.aliases) == 1
    alias = table.aliases[0]
    assert alias.local_name == "a"
    assert alias.target_module == "a.b.c"


def test_conditional_import_flagged(tmp_path):
    source = (
        "try:\n"
        "    import ujson as json\n"
        "except ImportError:\n"
        "    import json\n"
        "\n"
        "try:\n"
        "    import fastthing\n"
        "except ModuleNotFoundError:\n"
        "    import slowthing\n"
        "\n"
        "import os\n"
    )
    table = _table_for_source(tmp_path, source)

    json_aliases = [a for a in table.aliases if a.target_module in ("ujson", "json")]
    assert len(json_aliases) == 2
    assert all(a.conditional is True for a in json_aliases)

    modulenotfound_aliases = [a for a in table.aliases if a.target_module in ("fastthing", "slowthing")]
    assert len(modulenotfound_aliases) == 2
    assert all(a.conditional is True for a in modulenotfound_aliases)

    os_alias = next(a for a in table.aliases if a.target_module == "os")
    assert os_alias.conditional is False


def test_type_checking_import_flagged(tmp_path):
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from a.b import C\n"
    )
    table = _table_for_source(tmp_path, source, first_party_roots={"a"})

    c_alias = next(a for a in table.aliases if a.local_name == "C")
    assert c_alias.type_checking_only is True

    type_checking_alias = next(a for a in table.aliases if a.local_name == "TYPE_CHECKING")
    assert type_checking_alias.type_checking_only is False
