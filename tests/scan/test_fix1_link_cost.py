import reachability.scan.link as link
from tests.scan.s15_support import link_tree

CLS = "class C:\n    def m(self, a):\n        return a\n\n\n"


def test_only_prefiltered_modules_are_parsed(tmp_path, monkeypatch):
    files = {
        # LOW ?:m edge and a module-level `x = C(` candidate: parsed
        "cand.py": CLS + "x = C()\n\n\ndef use(a):\n    return x.m(a)\n",
        # LOW ?:m edge but no module-level candidate: not parsed
        "lowonly.py": "def use(a, b):\n    return b.m(a)\n",
        # candidate text but no LOW edge: not parsed
        "nolow.py": CLS + "y = C()\n",
        "plain.py": "def f():\n    return 1\n",
    }
    parsed = []
    real = link._parse

    def counting(path):
        parsed.append(path.name)
        return real(path)

    monkeypatch.setattr(link, "_parse", counting)
    _, res = link_tree(tmp_path, files)
    assert parsed == ["cand.py"]
    assert res.files_parsed == 1
    assert res.files_skipped == 0
