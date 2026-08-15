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


# The suite must not read the developer's cloud configuration.
#
# ``Settings.from_env()`` loads ``.env.local`` and then ``.env``, which is right
# for running the product and wrong for testing it. The moment a real
# ``GOOGLE_CLOUD_PROJECT`` and ``ASSURANCEOS_MODEL_MODE=vertex`` were configured
# to record the demonstration, two tests went red — not because anything broke,
# but because assertions about component status describe a deployment that now
# looked different. CI, which has no such file, stayed green on the same commit.
# That is the worse half of the problem: the result depended on an untracked
# file, so the suite disagreed with itself across machines.
#
# Neutralised here rather than patched test by test, because the next variable
# added would reintroduce the same class. A test that needs a particular model
# mode sets it itself, and is then testing something it has stated.
#
# **Set to empty, never deleted.** Deleting looks equivalent and is not:
# ``Settings.from_env()`` calls ``load_dotenv(override=False)``, which fills any
# variable that is *absent* and leaves any variable that is *present* alone. So a
# popped name is re-populated from `.env.local` the moment config is imported,
# and the neutralisation silently does nothing. That is exactly how the
# OpenTelemetry test kept failing after this block was added: `GOOGLE_CLOUD_PROJECT`
# came back, the Cloud Trace exporter installed a global tracer provider first,
# and OpenTelemetry honours only the first one — so the test's own in-memory
# exporter received nothing while its "is otel enabled" guard still saw a
# provider and declined to skip.
_AMBIENT_MODEL_ENV = (
    "ASSURANCEOS_GEMINI_MODEL",
    "ASSURANCEOS_GEMINI_LOCATION",
    "ASSURANCEOS_LOCAL_MODEL_URL",
    "GOOGLE_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_CLOUD_STAGING_BUCKET",
)
for _name in _AMBIENT_MODEL_ENV:
    os.environ[_name] = ""
os.environ["ASSURANCEOS_MODEL_MODE"] = "mock"


def alembic_head() -> str:
    """The current migration head, resolved rather than pinned as a literal."""
    return ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()


@pytest.fixture(scope="session")
def current_head() -> str:
    return alembic_head()
