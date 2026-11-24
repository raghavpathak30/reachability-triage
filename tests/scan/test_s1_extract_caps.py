import io
import zipfile

import pytest

from reachability.scan import wheel_extract as we
from reachability.scan.wheel_extract import WheelRejected, extract_wheel
from tests.scan.test_s1_extract_rules import make_wheel


def test_too_many_members(tmp_path):
    whl = tmp_path / "w.whl"
    with zipfile.ZipFile(whl, "w") as z:
        for i in range(we.MAX_MEMBERS + 1):
            z.writestr(f"p/m{i}.py", "")
    with pytest.raises(WheelRejected, match="too many members"):
        extract_wheel(whl, tmp_path / "out")
    assert not list((tmp_path / "out").rglob("*.py"))


def test_py_file_over_5mb(tmp_path):
    whl = make_wheel(tmp_path / "w.whl", [("p/big.py", b"#" * (5 * 1024 * 1024 + 1), None)])
    with pytest.raises(WheelRejected, match="exceeds"):
        extract_wheel(whl, tmp_path / "out")


def test_py_file_at_limit_ok(tmp_path):
    whl = make_wheel(tmp_path / "w.whl", [("p/ok.py", b"#" * (5 * 1024 * 1024), None)])
    extract_wheel(whl, tmp_path / "out")
    assert (tmp_path / "out" / "p" / "ok.py").is_file()


def test_total_cap_counts_streamed_bytes_not_declared_sizes(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "MAX_EXTRACTED_BYTES", 1024 * 1024)
    whl = make_wheel(tmp_path / "w.whl", [("p/a.py", b"x = 1\n", None)])
    assert zipfile.ZipFile(whl).infolist()[0].file_size == 6  # the header claims 6 bytes

    real_open = zipfile.ZipFile.open

    def lying_open(self, name, *a, **k):
        return io.BytesIO(b"#" * (2 * 1024 * 1024))

    monkeypatch.setattr(zipfile.ZipFile, "open", lying_open)
    with pytest.raises(WheelRejected, match="extracted size"):
        extract_wheel(whl, tmp_path / "out")
    monkeypatch.setattr(zipfile.ZipFile, "open", real_open)


def test_only_py_and_record_written(tmp_path):
    whl = make_wheel(
        tmp_path / "w.whl",
        [
            ("p/__init__.py", b"", None),
            ("p/data.json", b"{}", None),
            ("p/_native.so", b"\x7fELF", None),
            ("p-1.0.dist-info/RECORD", b"p/__init__.py,,\n", None),
            ("p-1.0.dist-info/METADATA", b"Name: p\n", None),
            ("p-1.0.data/scripts/run", b"#!/bin/sh", None),
        ],
    )
    extract_wheel(whl, tmp_path / "out")
    written = sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert written == ["p-1.0.dist-info/RECORD", "p/__init__.py"]
