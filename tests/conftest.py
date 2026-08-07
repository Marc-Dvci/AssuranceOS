"""Shared test fixtures.

Helpers live here rather than being imported across test modules. ``tests`` is not
a package, so ``from tests.test_migrations import ...`` only resolves when the
repository root happens to be on ``sys.path`` — which ``python -m pytest`` arranges
and a bare ``pytest`` does not. conftest is loaded by pytest itself and does not
depend on that difference.
"""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


def alembic_head() -> str:
    """The current migration head, resolved rather than pinned as a literal."""
    return ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()


@pytest.fixture(scope="session")
def current_head() -> str:
    return alembic_head()
