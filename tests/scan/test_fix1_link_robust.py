import reachability.scan.link as link
from tests.scan.s15_support import link_tree, triples

CLS = "class C:\n    def m(self, a):\n        return a\n\n\n"


def _files():
    return {
        "bad.py": CLS + "x = C()\n\n\ndef use(a):\n    return x.m(a)\n",
        "good.py": CLS + "y = C()\n\n\ndef use(a):\n    return y.m(a)\n",
    }


def test_recursion_error_in_one_file_does_not_drop_other_edges(tmp_path, monkeypatch):
    real = link._parse

    def parse(path):
        if path.name == "bad.py":
            raise RecursionError("deep")
        return real(path)

    monkeypatch.setattr(link, "_parse", parse)
    _, res = link_tree(tmp_path, _files())
    assert ("good:use", "good:C.m", 10, "module_singleton") in triples(res)
    assert not any(e.caller_id.startswith("bad:") and e.resolution_rule.value == "module_singleton" for e in res.edges)
    assert res.files_skipped == 1


def test_parse_honours_pep263_cookie(tmp_path):
    path = tmp_path / "m.py"
    path.write_bytes("# -*- coding: latin-1 -*-\nx = '\xe9'\n".encode("latin-1"))
    assert link._parse(path).body
