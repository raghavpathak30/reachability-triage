import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "scan_inventory.py"
FIX = REPO / "tests" / "fixtures" / "app_scan"
HINT = (
    "We need exact versions and never run a resolver. Create a lockfile where you run the app, "
    "then scan again: `uv lock` (uv), `poetry lock` (Poetry), or `pip freeze > requirements.txt` "
    "run inside the environment you deploy."
)


def records(*ids):
    return list(ids)


def aff(name, versions):
    return {"package": {"name": name, "ecosystem": "PyPI"}, "versions": versions}


def cli(root, snapshot, sha=None, *extra):
    cmd = [sys.executable, str(SCRIPT), str(root), "--snapshot", str(snapshot)]
    if sha:
        cmd += ["--snapshot-sha256", sha]
    return subprocess.run(cmd + list(extra), capture_output=True, text=True)


def section_index(lines, header):
    return next(i for i, line in enumerate(lines) if line == header)


@pytest.fixture
def mini(snapshot_factory):
    return snapshot_factory(sorted(p.stem for p in (FIX / "advisories").glob("*.json")))


def test_cli_prints_sections_in_exact_order_with_lead_first(mini):
    path, sha = mini
    proc = cli(FIX / "lockfiles" / "T9", path, sha)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    label = json.loads((FIX / "eval" / "T9" / "label.json").read_text())
    assert lines[0] == label["summary_lead"]
    assert lines[1].startswith("advisories as of 2026-11-12 (snapshot sha256 " + sha + ")")
    idx = [section_index(lines, h) for h in ("== Malicious packages ==", "== Inventory ==", "== Skipped and ignored ==", "== Hint ==")]
    assert idx == sorted(idx) and idx[0] == 2
    assert lines[-1] == HINT and lines[-2] == "== Hint =="


def test_cli_no_lead_line_when_all_pinned_first_line_is_advisories_as_of(snapshot_factory, scan_root):
    path, sha = snapshot_factory([{"id": "A-1", "aliases": [], "affected": [aff("foo", ["9"])]}])
    (scan_root / "requirements.txt").write_text("foo==1.0\n")
    proc = cli(scan_root, path, sha)
    assert proc.returncode == 0
    lines = proc.stdout.splitlines()
    assert lines[0].startswith("advisories as of 2026-11-12 (snapshot sha256 ")
    assert "== Hint ==" not in lines


def test_cli_malicious_section_before_inventory(snapshot_factory):
    path, sha = snapshot_factory(["MAL-2023-10"])
    proc = cli(FIX / "lockfiles" / "M1", path, sha)
    lines = proc.stdout.splitlines()
    m = section_index(lines, "== Malicious packages ==")
    inv = section_index(lines, "== Inventory ==")
    assert m < inv
    assert lines[m + 1] == "MAL-2023-10 | security-util-py | 0.0.6 | mal_prefix"
    assert "none" not in lines[m + 1 : inv]


def test_cli_malicious_none_when_empty(snapshot_factory, scan_root):
    path, sha = snapshot_factory([{"id": "A-1", "aliases": [], "affected": []}])
    (scan_root / "requirements.txt").write_text("foo==1.0\n")
    lines = cli(scan_root, path, sha).stdout.splitlines()
    m = section_index(lines, "== Malicious packages ==")
    assert lines[m + 1] == "none"


def test_cli_table_columns_and_fixed_in_none_explicit(snapshot_factory, scan_root):
    path, sha = snapshot_factory(
        [
            {"id": "A-1", "aliases": [], "affected": [aff("foo", ["1.0"])]},
            {"id": "B-1", "aliases": [], "affected": [{"package": {"name": "bar", "ecosystem": "PyPI"},
                                                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.0"}]}]}]},
        ]
    )
    (scan_root / "requirements.txt").write_text("foo==1.0\nbar==2.0\nbaz==1.0\n")
    lines = cli(scan_root, path, sha).stdout.splitlines()
    inv = section_index(lines, "== Inventory ==")
    assert lines[inv + 1] == "dist | version | status | advisory ids | fixed-in"
    rows = lines[inv + 2 : section_index(lines, "== Skipped and ignored ==")]
    assert "foo | 1.0 | pinned | A-1 | none" in rows
    assert "bar | 2.0 | pinned | B-1 | 3.0" in rows
    assert "baz | 1.0 | pinned | - | -" in rows


def test_cli_unpinned_rows_show_file_and_line(mini):
    path, sha = mini
    lines = cli(FIX / "lockfiles" / "T9", path, sha).stdout.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("accelerate | - | unpinned"))
    assert lines[i + 1] == "    at requirements.txt:2"
    j = next(n for n, line in enumerate(lines) if line.startswith("- | - | unsupported"))
    assert lines[j + 1] == "    at requirements.txt:3"
    assert any(line.startswith("    note: ") for line in lines[j + 1 : j + 4])
    k = next(n for n, line in enumerate(lines) if line.startswith("pywin32 | 306 | marker_skipped"))
    assert lines[k + 1] == "    at requirements.txt:7"


def test_cli_at_file_without_line_for_lock_formats(snapshot_factory):
    path, sha = snapshot_factory([{"id": "A-1", "aliases": [], "affected": []}])
    lines = cli(FIX / "lockfiles" / "uv_basic", path, sha).stdout.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("colorama | 0.4.6 | marker_skipped"))
    assert lines[i + 1] == "    at uv.lock"


def test_cli_skipped_section_lists_symlink_outside_include_and_ignored_lockfiles(snapshot_factory, tmp_path, scan_root):
    path, sha = snapshot_factory([{"id": "A-1", "aliases": [], "affected": []}])
    (tmp_path / "outside.txt").write_text("x==1\n")
    (scan_root / "real.txt").write_text("y==1\n")
    os.symlink(scan_root / "real.txt", scan_root / "link.txt")
    (scan_root / "requirements.txt").write_text("-r ../outside.txt\n-r link.txt\nfoo==1.0\n")
    (scan_root / "Pipfile.lock").write_text("{}")
    lines = cli(scan_root, path, sha).stdout.splitlines()
    sk = lines[section_index(lines, "== Skipped and ignored ==") + 1 :]
    text = "\n".join(sk)
    assert "include_outside_root | ../outside.txt | at requirements.txt:1" in text
    assert "symlink | link.txt | at requirements.txt:2" in text
    assert "ignored_lockfile | Pipfile.lock" in text and "format arrives in A1b" in text


def test_cli_skipped_none_when_empty(snapshot_factory, scan_root):
    path, sha = snapshot_factory([{"id": "A-1", "aliases": [], "affected": []}])
    (scan_root / "requirements.txt").write_text("foo==1.0\n")
    lines = cli(scan_root, path, sha).stdout.splitlines()
    assert lines[section_index(lines, "== Skipped and ignored ==") + 1] == "none"


def test_cli_hint_last_and_only_when_set(mini, scan_root):
    path, sha = mini
    out = cli(FIX / "lockfiles" / "T10", path, sha).stdout.splitlines()
    assert out[-2:] == ["== Hint ==", HINT]
    assert out.count("== Hint ==") == 1
    empty = cli(scan_root, path, sha)
    assert empty.returncode == 0
    assert empty.stdout.splitlines()[-2:] == ["== Hint ==", HINT]


def test_cli_exit_0_with_advisories_found(snapshot_factory, scan_root):
    path, sha = snapshot_factory([{"id": "A-1", "aliases": [], "affected": [aff("foo", ["1.0"])]}])
    (scan_root / "requirements.txt").write_text("foo==1.0\n")
    proc = cli(scan_root, path, sha)
    assert proc.returncode == 0 and "A-1" in proc.stdout


def test_cli_exit_0_when_no_lockfile(mini, scan_root):
    path, sha = mini
    assert cli(scan_root, path, sha).returncode == 0


def test_cli_inventory_notes_listed(snapshot_factory):
    path, sha = snapshot_factory([{"id": "A-1", "aliases": [], "affected": []}])
    out = cli(FIX / "lockfiles" / "uv_extras_markers", path, sha).stdout
    assert "edge to root member" in out
