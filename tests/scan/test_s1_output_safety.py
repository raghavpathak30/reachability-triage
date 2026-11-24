import json
import os
import re

from reachability import cli
from reachability.scan.fetch import LocalDirFetcher
from reachability.scan.pipeline import run_scan
from reachability.scan.render import render_json, render_text
from tests.scan.s1_support import APP_CALLS_F, LIB_NEW, LIB_OLD, advisory, lib_files, make_scan

CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _scan(tmp_path):
    adv = advisory(
        "GHSA-a\x1b[2Jbbb-cccc-dddd", "lib", summary="boom \x1b[2J \x85 \x9b",
        aliases=["CVE-\x1b[31m2026-1", "PYSEC-\x85-2"],
    )
    s = make_scan(
        tmp_path,
        app_files={"main.py": APP_CALLS_F},
        pins={"lib": "1.0"},
        wheels={("lib", "1.0"): lib_files(LIB_OLD), ("lib", "2.0"): lib_files(LIB_NEW)},
        advisories=[adv],
    )
    os.symlink(s["app"] / "main.py", s["app"] / "e\x1b[2Jv\x85il.py")  # package-supplied string in a Gap
    return s


def test_control_characters_stripped_in_text_and_json(tmp_path):
    s = _scan(tmp_path)
    res = run_scan(s["app"], s["snapshot"], s["sha256"], LocalDirFetcher(s["wheels"]))
    text, js = render_text(res), render_json(res)
    for out in (text, js):
        assert "\x1b" not in out and "\x85" not in out and "\x9b" not in out
        assert not CONTROL.search(out)
    assert "[2J" in js  # only the control bytes are removed, the visible text stays
    data = json.loads(js)
    assert data["findings"][0]["advisory_id"] == "GHSA-a[2Jbbb-cccc-dddd"
    assert any("[2Jv" in g["detail"] for g in data["gaps"])


def test_cli_output_is_clean(tmp_path, capsys):
    s = _scan(tmp_path)
    for fmt in ("text", "json"):
        assert cli.main(
            ["scan", str(s["app"]), "--snapshot", str(s["snapshot"]), "--snapshot-sha256", s["sha256"],
             "--wheel-dir", str(s["wheels"]), "--format", fmt]
        ) == 0
        assert not CONTROL.search(capsys.readouterr().out)


def test_error_message_is_clean(tmp_path, capsys):
    bad = tmp_path / "no\x1b[2Jpe" / "osv-pypi-2026-11-12.zip"
    assert cli.main(["scan", str(tmp_path), "--snapshot", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "\x1b" not in err and err.startswith("error:")
