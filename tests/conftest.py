import os
import socket
import subprocess
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from reachability.db.session import reset_engine_for_tests


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel_path, content in files.items():
        p = root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


_PG_BIN_DIR = "/usr/lib/postgresql/17/bin"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _pg_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{_PG_BIN_DIR}:{env.get('PATH', '')}"
    return env


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture(scope="session")
def postgres_cluster(tmp_path_factory):
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        # CI-services-block case: DATABASE_URL points at a freshly started,
        # unmigrated `postgres:17` service container -- `alembic upgrade
        # head` must still run here (it's a no-op if already migrated), or
        # every DB-dependent test would fail with "relation triage_jobs
        # does not exist". Only initdb/pg_ctl cluster lifecycle management
        # is skipped in this branch, not the migration itself -- one
        # fixture, two backing environments, no CI-only special-casing of
        # the tests themselves.
        reset_engine_for_tests()
        command.upgrade(_alembic_config(database_url), "head")
        yield database_url
        return

    env = _pg_env()
    pgdata = tmp_path_factory.mktemp("pgdata")
    subprocess.run(
        ["initdb", "-D", str(pgdata), "-U", "postgres", "--auth=trust", "-E", "UTF8"],
        env=env,
        check=True,
        capture_output=True,
    )
    port = _free_tcp_port()
    logfile = pgdata / "server.log"
    subprocess.run(
        [
            "pg_ctl",
            "-D",
            str(pgdata),
            "-l",
            str(logfile),
            "-o",
            f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories=",
            "start",
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    database_url = f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"
    os.environ["DATABASE_URL"] = database_url
    reset_engine_for_tests()
    try:
        command.upgrade(_alembic_config(database_url), "head")
        yield database_url
    finally:
        os.environ.pop("DATABASE_URL", None)
        reset_engine_for_tests()
        subprocess.run(
            ["pg_ctl", "-D", str(pgdata), "stop", "-m", "fast"],
            env=env,
            check=True,
            capture_output=True,
        )


@pytest.fixture
def db_session(postgres_cluster):
    reset_engine_for_tests()
    engine = create_engine(postgres_cluster)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.execute(text("TRUNCATE triage_jobs"))
        session.commit()
        session.close()
        engine.dispose()
