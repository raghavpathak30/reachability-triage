import sys
from pathlib import Path

import pytest

from tests.conftest import write_tree  # noqa: F401  (shared helper)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import materialize_scan_fixture as mat  # noqa: E402

from reachability.scan.combined import CombinedIndexError, build_combined  # noqa: E402
from reachability.scan.fetch import LocalDirFetcher, fetch_dists  # noqa: E402


def test_e1_combined_has_app_and_dist_modules(tmp_path):
    r = mat.materialize_e1(tmp_path / "m")
    res = fetch_dists([("langchain-openai", "1.1.13")], LocalDirFetcher(r["wheels"]), tmp_path / "store")
    root = res.dists[("langchain-openai", "1.1.13")].root
    c = build_combined(r["app"], {"langchain-openai": root})
    try:
        names = {m.dotted_name for m in c.index.report.modules}
        assert "main" in names and "langchain_openai.chat_models.base" in names
        assert c.app_modules == {"main"}
        assert c.module_origins()["langchain_openai.chat_models.base"] == "langchain-openai"
    finally:
        c.close()


def test_open_webui_shaped_tree(tmp_path):
    app = tmp_path / "app"
    write_tree(
        app,
        {
            "src/app.html": "<html/>",
            "src/app.css": "x",
            "backend/open_webui/__init__.py": "",
            "backend/open_webui/models.py": "x = 1\n",
            "backend/open_webui/utils/__init__.py": "",
            "backend/open_webui/utils/auth.py": "from open_webui.models import x\n",
            "scripts/x.py": "y = 2\n",
        },
    )
    c = build_combined(app, {})
    try:
        names = c.app_modules
        assert "open_webui.utils.auth" in names and "scripts.x" in names
        assert not any(n.startswith("backend") or n.startswith("src") for n in names)
        assert not (c.root / "src").exists()
    finally:
        c.close()


def test_flaskr_shaped_tree(tmp_path):
    app = tmp_path / "app"
    write_tree(
        app,
        {
            "flaskr/__init__.py": "",
            "flaskr/auth.py": "def login(): pass\n",
            "tests/conftest.py": "",
            "tests/test_auth.py": "def test_x(): pass\n",
        },
    )
    c = build_combined(app, {})
    try:
        assert {"flaskr", "flaskr.auth", "tests.test_auth"} <= c.app_modules
    finally:
        c.close()


def test_src_layout(tmp_path):
    app = tmp_path / "app"
    write_tree(app, {"src/mypkg/__init__.py": "", "src/mypkg/core.py": "a = 1\n"})
    c = build_combined(app, {})
    try:
        assert "mypkg.core" in c.app_modules
    finally:
        c.close()


def test_temp_root_with_test_component_is_tool_error(tmp_path):
    base = tmp_path / "tests"
    base.mkdir()
    app = tmp_path / "app"
    write_tree(app, {"a.py": "x = 1\n"})
    with pytest.raises(CombinedIndexError, match="TMPDIR"):
        build_combined(app, {}, tmp_parent=base)
