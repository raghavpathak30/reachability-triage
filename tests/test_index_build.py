from reachability.index.build import build_l1_index
from reachability.index.models import ModuleKind, TargetKind

from conftest import write_tree


def test_syntax_error_file_marked_unparsed_and_run_continues(tmp_path):
    write_tree(
        tmp_path,
        {
            "good.py": "import os\n",
            "bad.py": "def broken(:\n",
        },
    )

    report = build_l1_index(tmp_path)

    assert len(report.unparsed) == 1
    assert report.unparsed[0].file_path == tmp_path / "bad.py"
    assert "good" in report.import_tables


def test_l1_gate_fixture_exact_alias_table(tmp_path):
    write_tree(
        tmp_path,
        {
            "src/a/__init__.py": "",
            "src/a/b/__init__.py": "",
            "src/a/b/c.py": (
                "from .. import x\n"
                "import numpy as np\n"
                "from a.b import c as d\n"
                "from a.b import *\n"
            ),
        },
    )

    report = build_l1_index(tmp_path)

    dotted_names = {m.dotted_name for m in report.modules}
    assert dotted_names == {"a", "a.b", "a.b.c"}

    table = report.import_tables["a.b.c"]
    assert len(table.aliases) == 3

    by_local_name = {a.local_name: a for a in table.aliases}

    x_alias = by_local_name["x"]
    assert x_alias.target_module is None
    assert x_alias.target_symbol == "x"
    assert x_alias.is_relative is True
    assert x_alias.resolved_absolute == "a"
    assert x_alias.target_kind == TargetKind.FIRST_PARTY

    np_alias = by_local_name["np"]
    assert np_alias.target_module == "numpy"
    assert np_alias.target_symbol is None
    assert np_alias.is_relative is False
    assert np_alias.resolved_absolute == "numpy"
    assert np_alias.target_kind == TargetKind.THIRD_PARTY

    d_alias = by_local_name["d"]
    assert d_alias.target_module == "a.b"
    assert d_alias.target_symbol == "c"
    assert d_alias.is_relative is False
    assert d_alias.resolved_absolute == "a.b"
    assert d_alias.target_kind == TargetKind.FIRST_PARTY

    assert len(table.star_imports) == 1
    star = table.star_imports[0]
    assert star.module == "a.b"
    assert star.resolved_absolute == "a.b"
    assert star.target_kind == TargetKind.FIRST_PARTY

    assert report.star_import_count == 1


def test_init_reexport_one_hop_resolved(tmp_path):
    write_tree(
        tmp_path,
        {
            # One-hop resolve
            "pkg/__init__.py": "from .core import Thing\n",
            "pkg/core.py": "",
            "importer.py": "from pkg import Thing\n",
            # Chained — stops at one hop
            "pkg2/__init__.py": "from .sub import Thing2\n",
            "pkg2/sub/__init__.py": "from .impl import Thing2\n",
            "pkg2/sub/impl.py": "",
            "importer2.py": "from pkg2 import Thing2\n",
            # Ambiguity / collision — unresolved
            "pkg3/__init__.py": (
                "from .a import Thing3\n"
                "from .b import Thing3 as Thing3\n"
            ),
            "pkg3/a.py": "",
            "pkg3/b.py": "",
            "importer3.py": "from pkg3 import Thing3\n",
            # TYPE_CHECKING guard — unresolved
            "pkg4/__init__.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    from .core import Thing4\n"
            ),
            "pkg4/core.py": "",
            "importer4.py": "from pkg4 import Thing4\n",
        },
    )

    report = build_l1_index(tmp_path)

    # --- one-hop resolve ---
    importer_alias = next(
        a for a in report.import_tables["importer"].aliases if a.local_name == "Thing"
    )
    assert importer_alias.resolved_absolute == "pkg.core"
    assert importer_alias.via_reexport is True

    pkg_own_alias = next(
        a for a in report.import_tables["pkg"].aliases if a.local_name == "Thing"
    )
    assert pkg_own_alias.resolved_absolute == "pkg.core"
    assert pkg_own_alias.via_reexport is False

    # --- chained: stops at one hop ---
    importer2_alias = next(
        a for a in report.import_tables["importer2"].aliases if a.local_name == "Thing2"
    )
    assert importer2_alias.resolved_absolute == "pkg2.sub"
    assert importer2_alias.via_reexport is True

    pkg2_own_alias = next(
        a for a in report.import_tables["pkg2"].aliases if a.local_name == "Thing2"
    )
    assert pkg2_own_alias.resolved_absolute == "pkg2.sub"
    assert pkg2_own_alias.via_reexport is False

    pkg2_sub_own_alias = next(
        a for a in report.import_tables["pkg2.sub"].aliases if a.local_name == "Thing2"
    )
    assert pkg2_sub_own_alias.resolved_absolute == "pkg2.sub.impl"
    assert pkg2_sub_own_alias.via_reexport is False

    # --- ambiguity / collision: unresolved ---
    importer3_alias = next(
        a for a in report.import_tables["importer3"].aliases if a.local_name == "Thing3"
    )
    assert importer3_alias.resolved_absolute == "pkg3"
    assert importer3_alias.via_reexport is False

    # --- TYPE_CHECKING guard: unresolved ---
    importer4_alias = next(
        a for a in report.import_tables["importer4"].aliases if a.local_name == "Thing4"
    )
    assert importer4_alias.resolved_absolute == "pkg4"
    assert importer4_alias.via_reexport is False
