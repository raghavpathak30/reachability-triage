from reachability.index import build_l1_index, build_symbol_index
from reachability.index.symbol_models import NodeKind
from reachability.scan.targets import symbols_from_tables
from tests.conftest import write_tree


def test_symdef_end_lineno_and_kind(tmp_path):
    src = "import functools\n\n\n@functools.cache\ndef f():\n    return 1\n\n\nclass C:\n    def m(self):\n        pass\n\n\nasync def g():\n    pass\n"
    write_tree(tmp_path, {"m.py": src})
    syms = symbols_from_tables(build_symbol_index(build_l1_index(tmp_path)))
    d = {k[1]: v for k, v in syms.defs.items()}
    assert (d["f"].start_lineno, d["f"].lineno, d["f"].end_lineno) == (4, 5, 6)
    assert d["f"].kind is NodeKind.FUNCTION
    assert d["C.m"].kind is NodeKind.METHOD and d["C.m"].end_lineno == 11
    assert d["C"].kind is NodeKind.CLASS and d["C"].end_lineno == 11
    assert d["g"].kind is NodeKind.ASYNCFUNCTION
