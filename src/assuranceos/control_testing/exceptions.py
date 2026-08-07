class ControlTestError(RuntimeError):
    """Base error for deterministic control testing."""


class TestPackageError(ControlTestError):
    pass


class TestReleaseNotFoundError(ControlTestError):
    pass


class TestReleaseConflictError(ControlTestError):
    pass


class TestInputValidationError(ControlTestError):
    pass


class TestExecutionError(ControlTestError):
    pass


class TestExecutionTimeoutError(TestExecutionError):
    pass


class TestOutputValidationError(ControlTestError):
    pass


class TestRunNotFoundError(ControlTestError):
    pass


class ReproducibilityMismatchError(ControlTestError):
    pass
