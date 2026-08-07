"""Failures in the adjudication lifecycle.

Each is a distinct refusal so a caller can tell a governance denial apart from a
missing record. They carry the attributable reason, because in this component the
reason is the product: an audit that cannot say why something was rejected has not
rejected it defensibly.
"""

from __future__ import annotations


class AdjudicationError(Exception):
    """Base class for every adjudication refusal."""


class FindingNotFoundError(AdjudicationError):
    pass


class RemediationNotFoundError(AdjudicationError):
    pass


class InvalidTransitionError(AdjudicationError):
    """The lifecycle does not permit this move.

    Raised rather than silently coercing the status, so a caller that has
    mis-sequenced the workflow finds out at the point of the mistake.
    """

    def __init__(self, current: str, requested: str):
        super().__init__(
            f"a finding in {current!r} cannot move to {requested!r}"
        )
        self.current = current
        self.requested = requested


class HumanGateError(AdjudicationError):
    """An approval was attributed to something other than a person."""


class IndependenceError(AdjudicationError):
    """The retester is not independent of the work being retested.

    Separation of duties is the whole basis on which a retest carries assurance.
    A retest performed by the author of the finding or the owner of the
    remediation is not weaker evidence; it is not evidence.
    """


class ClosureEvidenceError(AdjudicationError):
    """A closure was asserted without the evidence the action requires."""


class IdempotencyConflictError(AdjudicationError):
    """An idempotency key was reused with different inputs."""
