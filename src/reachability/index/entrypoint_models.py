from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class EntrypointSource(str, Enum):
    DUNDER_MAIN = "dunder_main"
    CONSOLE_SCRIPT = "console_script"
    ROUTE_DECORATOR = "route_decorator"
    CELERY_TASK = "celery_task"
    CLI_DECORATOR = "cli_decorator"
    TEST_FUNCTION = "test_function"
    PYTEST_FIXTURE = "pytest_fixture"


@dataclass(frozen=True)
class Entrypoint:
    source: EntrypointSource
    node_id: str
    module: str
    file: Path
    lineno: int
    is_test: bool
