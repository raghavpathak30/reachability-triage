from reachability.index.build import build_l1_index
from reachability.index.entrypoint_models import EntrypointSource
from reachability.index.entrypoints import build_entrypoint_index
from reachability.index.symbols import build_symbol_index

from conftest import write_tree


def _build_entrypoints(tmp_path, files):
    write_tree(tmp_path, files)
    report = build_l1_index(tmp_path)
    symbol_index = build_symbol_index(report)
    return build_entrypoint_index(report, symbol_index, tmp_path)


def test_dunder_main_entrypoint_detected(tmp_path):
    entrypoints = _build_entrypoints(
        tmp_path,
        {
            "mod.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    helper()\n"
            )
        },
    )

    ep = next(e for e in entrypoints if e.source == EntrypointSource.DUNDER_MAIN)
    assert ep.node_id == "mod"
    assert ep.module == "mod"
    assert ep.lineno == 4
    assert ep.is_test is False


def test_console_script_entrypoint_resolves_to_real_function(tmp_path):
    entrypoints = _build_entrypoints(
        tmp_path,
        {
            "pyproject.toml": ('[project.scripts]\nmycli = "mymod:main_func"\n'),
            "mymod.py": "def main_func():\n    return 1\n",
        },
    )

    ep = next(e for e in entrypoints if e.source == EntrypointSource.CONSOLE_SCRIPT)
    assert ep.node_id == "mymod:main_func"
    assert ep.module == "mymod"
    assert ep.is_test is False


def test_console_script_entrypoint_skipped_when_target_missing(tmp_path):
    entrypoints = _build_entrypoints(
        tmp_path,
        {
            "pyproject.toml": ('[project.scripts]\nmycli = "mymod:missing_func"\n'),
            "mymod.py": "def main_func():\n    return 1\n",
        },
    )

    assert not any(e.source == EntrypointSource.CONSOLE_SCRIPT for e in entrypoints)


def test_route_decorator_entrypoint_detected(tmp_path):
    entrypoints = _build_entrypoints(
        tmp_path,
        {
            "mod.py": (
                '@app.get("/x")\n'
                "def handler():\n"
                "    return 1\n"
            )
        },
    )

    ep = next(e for e in entrypoints if e.source == EntrypointSource.ROUTE_DECORATOR)
    assert ep.node_id == "mod:handler"
    assert ep.is_test is False


def test_celery_task_entrypoint_detected(tmp_path):
    entrypoints = _build_entrypoints(
        tmp_path,
        {
            "mod.py": (
                "@celery.task\n"
                "def do_work():\n"
                "    return 1\n"
            )
        },
    )

    ep = next(e for e in entrypoints if e.source == EntrypointSource.CELERY_TASK)
    assert ep.node_id == "mod:do_work"
    assert ep.is_test is False


def test_cli_decorator_entrypoint_detected(tmp_path):
    entrypoints = _build_entrypoints(
        tmp_path,
        {
            "mod.py": (
                "@click.command()\n"
                "def cli_main():\n"
                "    return 1\n"
            )
        },
    )

    ep = next(e for e in entrypoints if e.source == EntrypointSource.CLI_DECORATOR)
    assert ep.node_id == "mod:cli_main"
    assert ep.is_test is False


def test_pytest_fixture_entrypoint_detected(tmp_path):
    entrypoints = _build_entrypoints(
        tmp_path,
        {
            "mod.py": (
                "@pytest.fixture\n"
                "def my_fixture():\n"
                "    return 1\n"
            )
        },
    )

    ep = next(e for e in entrypoints if e.source == EntrypointSource.PYTEST_FIXTURE)
    assert ep.node_id == "mod:my_fixture"
    assert ep.is_test is True


def test_test_function_entrypoint_detected(tmp_path):
    entrypoints = _build_entrypoints(
        tmp_path,
        {"test_stuff.py": "def test_foo():\n    assert True\n"},
    )

    ep = next(e for e in entrypoints if e.source == EntrypointSource.TEST_FUNCTION)
    assert ep.node_id == "test_stuff:test_foo"
    assert ep.is_test is True


def test_production_function_named_test_connection_not_detected(tmp_path):
    entrypoints = _build_entrypoints(
        tmp_path,
        {"mod.py": "def test_connection():\n    return True\n"},
    )

    assert not any(e.source == EntrypointSource.TEST_FUNCTION for e in entrypoints)
