from collections.abc import Iterator

import pytest

from inotify_simple import INotify


@pytest.fixture
def inotify() -> Iterator[INotify]:
    with INotify() as instance:
        yield instance
