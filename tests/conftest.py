"""Shared test fixtures.

Helpers live here rather than being imported across test modules. ``tests`` is not
a package, so ``from tests.test_migrations import ...`` only resolves when the
repository root happens to be on ``sys.path`` — which ``python -m pytest`` arranges
and a bare ``pytest`` does not. conftest is loaded by pytest itself and does not
depend on that difference.
"""

from __future__ import annotations

import os

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

# The deterministic control-test sandbox enforces memory and CPU ceilings through
# the POSIX ``resource`` interface and refuses to run where it cannot. That refusal
# is the intended production behaviour, but it also makes the suite unrunnable on
# Windows developer machines. Request the degraded sandbox explicitly, and only
# where the platform genuinely lacks the interface, so CI on Linux keeps exercising
# the enforced path rather than the waiver. ``test_production_hardening`` separately
# asserts that production configuration still rejects the degraded mode.
try:  # pragma: no cover - the branch taken depends on the host platform
    import resource  # noqa: F401
except ImportError:  # pragma: no cover - Windows developer machines
    os.environ.setdefault("ASSURANCEOS_CONTROL_TEST_ALLOW_DEGRADED_SANDBOX", "true")


def alembic_head() -> str:
    """The current migration head, resolved rather than pinned as a literal."""
    return ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()


@pytest.fixture(scope="session")
def current_head() -> str:
    return alembic_head()
