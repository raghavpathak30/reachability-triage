import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_ENGINE = None
_SESSIONMAKER = None


def get_sessionmaker() -> sessionmaker:
    """Lazily create and cache the engine/sessionmaker on first use.

    Reads `DATABASE_URL` from the environment at first call, not at import
    time, so tests can point it at the ephemeral cluster before any
    connection is opened.
    """
    global _ENGINE, _SESSIONMAKER
    if _SESSIONMAKER is None:
        _ENGINE = create_engine(os.environ["DATABASE_URL"])
        _SESSIONMAKER = sessionmaker(bind=_ENGINE)
    return _SESSIONMAKER


def reset_engine_for_tests() -> None:
    """Clear the cached engine/sessionmaker so a fresh `DATABASE_URL` is
    picked up on the next `get_sessionmaker()` call."""
    global _ENGINE, _SESSIONMAKER
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _SESSIONMAKER = None
