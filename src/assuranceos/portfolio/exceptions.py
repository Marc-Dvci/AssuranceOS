"""Refusals from the audit universe and the portfolio planner."""

from __future__ import annotations


class PortfolioError(Exception):
    """Base class for every portfolio refusal."""


class RiskNotFoundError(PortfolioError):
    pass


class PlanNotFoundError(PortfolioError):
    pass


class PlanStateError(PortfolioError):
    """The plan cannot move as requested, or the actor may not move it.

    Covers both the lifecycle refusal and the human-gate refusal, because from
    the caller's side they are the same shape: the plan is where it is, and this
    request does not change that.
    """


class CapacityError(PortfolioError):
    """The plan does not fit the capacity it declares.

    Approving a plan that cannot be delivered records a commitment nobody can
    keep, so it is refused rather than warned about.
    """
