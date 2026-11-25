from pathlib import Path

import pytest

from media_organizer.engine import Organizer
from media_organizer.geography import Places


class EmptyReader:
    executable = None

    def read_batch(self, paths):
        return {}


@pytest.fixture
def organizer(tmp_path):
    instance = Organizer(tmp_path / "state", places=Places(tmp_path / "no-places.sqlite3"), reader=EmptyReader())
    yield instance
    instance.close()


@pytest.fixture
def directories(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    return source, target
