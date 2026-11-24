"""F6: run_scan end to end for lock-file apps, --wheel-dir edge cases, partial_path, reason aggregation."""

import json

from reachability import cli
from reachability.scan.fetch import FILES_HOST, LocalDirFetcher
from reachability.scan.pipeline import run_scan
from tests.scan.s1_support import APP_CALLS_F, LIB_NEW, LIB_OLD, advisory, lib_files, make_scan

LOCK_WHL = "lib-1.0-py3-none-any.whl"


def _lock(registry, url, digest):
    return f'''version = 1
revision = 3
requires-python = ">=3.13"

[[package]]
name = "myapp"
version = "0.1.0"
source = {{ virtual = "." }}
dependencies = [
    {{ name = "lib" }},
]

[[package]]
name = "lib"
version = "1.0"
source = {{ registry = "{registry}" }}
wheels = [
    {{ url = "{url}", hash = "sha256:{digest}" }},
]
'''


def _uv_app(tmp_path, *, registry="https://pypi.org/simple", url=None, digest=None, drop_sidecar=False,
            app_files=None, advisories=None):
    s = make_scan(
        tmp_path,
        app_files=app_files or {"main.py": APP_CALLS_F},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=advisories or [advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    side = s["wheels"] / (LOCK_WHL + ".sha256")
    real = side.read_text().strip()
    if drop_sidecar:
        side.unlink()
    (s["app"] / "requirements.txt").unlink()
    (s["app"] / "uv.lock").write_text(
        _lock(registry, url or f"https://{FILES_HOST}/packages/ab/cd/{LOCK_WHL}", digest or real)
    )
    return s, real


class Recording(LocalDirFetcher):
    def __init__(self, d):
        super().__init__(d)
        self.json_urls, self.downloads = [], []

    def get_json(self, url):
        self.json_urls.append(url)
        return super().get_json(url)

    def download(self, url, dest, **kw):
        self.downloads.append(url)
        super().download(url, dest, **kw)


def _run(s, fetcher=None, **kw):
    return run_scan(s["app"], s["snapshot"], s["sha256"], fetcher or LocalDirFetcher(s["wheels"]), **kw)


def test_uv_lock_url_and_hash_honoured_end_to_end(tmp_path):
    s, _ = _uv_app(tmp_path, drop_sidecar=True)  # only the lock carries the digest
    f = Recording(s["wheels"])
    res = _run(s, f)
    assert res.findings[0].verdict == "reachable" and res.findings[0].evidence_level == "path_proven"
    assert any(u.endswith("/" + LOCK_WHL) and "/packages/ab/cd/" in u for u in f.downloads)
    assert not any("/lib/1.0/json" in u for u in f.json_urls)  # installed wheel never asked PyPI
    assert any("/lib/2.0/json" in u for u in f.json_urls)  # fix wheel comes from PyPI JSON


def test_uv_lock_wrong_hash_is_no_wheel(tmp_path):
    s, _ = _uv_app(tmp_path, digest="00" * 32)
    res = _run(s)
    f = res.findings[0]
    assert f.verdict == "unknown" and f.reason_code == "NO_WHEEL"
    assert any(g.kind == "no_wheel" and "sha256" in g.detail for g in res.gaps)


def test_uv_lock_non_pypi_registry_is_no_wheel_end_to_end(tmp_path):
    s, _ = _uv_app(tmp_path, registry="https://private.example/simple")
    f = Recording(s["wheels"])
    res = _run(s, f)
    assert res.findings[0].reason_code == "NO_WHEEL"
    assert any(g.kind == "no_wheel" and "private.example" in g.detail for g in res.gaps)
    assert not any("/lib/1.0/" in u for u in f.json_urls + f.downloads)
    row = next(p for p in res.packages if p["dist"] == "lib")
    assert row["wheel_status"] == "no_wheel"


def test_wheel_dir_missing_sidecar_is_no_wheel_no_digest(tmp_path):
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    (s["wheels"] / (LOCK_WHL + ".sha256")).unlink()
    res = _run(s)
    assert res.findings[0].reason_code == "NO_WHEEL"
    assert any(g.kind == "no_wheel" and "no digest" in g.detail for g in res.gaps)


def test_wheel_dir_with_uv_lock_row_uses_lock_url_basename_and_lock_hash(tmp_path, capsys):
    # With --wheel-dir the fetcher serves a lock URL by its basename from the directory; the lock's
    # hash, not the sidecar, is the expected digest. A wrong lock hash is a mismatch even if the sidecar is right.
    s, _ = _uv_app(tmp_path)
    args = ["scan", str(s["app"]), "--snapshot", str(s["snapshot"]), "--snapshot-sha256", s["sha256"],
            "--wheel-dir", str(s["wheels"]), "--format", "json"]
    assert cli.main(args) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["findings"][0]["verdict"] == "reachable"

    (s["app"] / "uv.lock").write_text(_lock("https://pypi.org/simple", f"https://{FILES_HOST}/p/{LOCK_WHL}", "00" * 32))
    assert cli.main(args) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["findings"][0]["reason_code"] == "NO_WHEEL"
    assert any("sha256 mismatch" in g["detail"] for g in d["gaps"])


LOW_APP = "def main(obj):\n    return obj.f()\n\n\nif __name__ == '__main__':\n    main(None)\n"


def test_partial_path_through_run_scan_for_engine_unknown_with_path(tmp_path):
    s = make_scan(
        tmp_path,
        app_files={"main.py": LOW_APP},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    res = _run(s)
    f = res.findings[0]
    assert f.verdict == "unknown" and f.reason_code == "LOW_CONFIDENCE_PATH" and f.evidence_level is None
    assert f.path is None and f.partial_path
    assert f.partial_path[0].file == "app/main.py"
    assert "not_reachable" not in json.dumps(f.to_dict())


def test_reason_aggregation_across_targets_through_run_scan(tmp_path):
    old = "def f(x):\n    return x + 1\n\n\ndef g(x):\n    return x + 1\n"
    new = "def f(x):\n    return x + 2\n\n\ndef g(x):\n    return x + 2\n"
    # f is reached only through an unresolved receiver (low confidence), g has no path at all.
    app = "from lib.core import helper\n\n\ndef main(obj):\n    obj.f()\n\n\nif __name__ == '__main__':\n    main(None)\n"
    lib_old = lib_files(old, {"lib/core.py": old + "\n\ndef helper():\n    return 0\n"})
    lib_new = lib_files(new, {"lib/core.py": new + "\n\ndef helper():\n    return 0\n"})
    s = make_scan(
        tmp_path,
        app_files={"main.py": app},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_old, ("lib", "2.0"): lib_new},
        advisories=[advisory("GHSA-aaaa-bbbb-cccc", "lib")],
    )
    res = _run(s)
    f = res.findings[0]
    assert len(f.targets) == 2
    assert f.reason_code == "LOW_CONFIDENCE_PATH"  # beats NO_PATH_FOUND from the other target
    assert f.partial_path
