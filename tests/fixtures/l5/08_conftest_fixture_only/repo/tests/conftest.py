import pytest

from pkg.sink import vulnerable


@pytest.fixture
def thing():
    vulnerable()
