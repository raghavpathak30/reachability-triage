import base64
import hashlib
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402


def test_materialize_e1(tmp_path):
    r = mat.materialize_e1(tmp_path)
    assert (r["app"] / "main.py").is_file()
    assert (r["app"] / "requirements.txt").read_text().strip() == "langchain-openai==1.1.13"
    for version in ("1.1.13", "1.1.14"):
        whl = r["wheels"] / f"langchain_openai-{version}-py3-none-any.whl"
        side = whl.with_name(whl.name + ".sha256")
        assert side.read_text().strip() == hashlib.sha256(whl.read_bytes()).hexdigest()
        with zipfile.ZipFile(whl) as z:
            assert "langchain_openai/chat_models/base.py" in z.namelist()
            record = z.read(f"langchain_openai-{version}.dist-info/RECORD").decode().splitlines()
            for line in record:
                name, digest, _ = line.split(",")
                if digest:
                    data = z.read(name)
                    want = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                    assert digest == f"sha256={want}"
    assert hashlib.sha256(r["snapshot"].read_bytes()).hexdigest() == r["sha256"]
    with zipfile.ZipFile(r["snapshot"]) as z:
        assert z.namelist() == ["GHSA-r7w7-9xr2-qq2r.json"]


def test_wheels_differ_in_url_to_size():
    assert mat.e1_wheel_files("1.1.13")["langchain_openai/chat_models/base.py"] != mat.e1_wheel_files("1.1.14")[
        "langchain_openai/chat_models/base.py"
    ]
