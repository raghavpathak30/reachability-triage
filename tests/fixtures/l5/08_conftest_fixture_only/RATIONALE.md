`vulnerable` is only ever called inside a pytest fixture in `conftest.py`. A pytest
fixture only runs when pytest collects and executes tests that request it — never in
a production run of this package.
