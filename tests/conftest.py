from typing import TYPE_CHECKING

import pytest

from inotify_simple import INotify

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def inotify() -> Iterator[INotify]:
    with INotify() as instance:
        yield instance
