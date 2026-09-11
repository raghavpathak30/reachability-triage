import pkg.decoy
from pkg.sink import vulnerable
from pkg.entry import run


def test_it(monkeypatch):
    monkeypatch.setattr(pkg.decoy, "safe", vulnerable)
    run()
