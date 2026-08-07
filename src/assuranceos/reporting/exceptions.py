"""Refusals from retrieval, the claim graph, and report rendering."""

from __future__ import annotations


class ReportingError(Exception):
    """Base class for every reporting refusal."""


class UnsupportedClaimError(ReportingError):
    """A material claim resolves to no admissible evidence and states no limitation.

    The refusal the component exists for. Raised with every unresolved issue
    rather than the first, because a partial list invites the belief that the last
    fix was the last problem.
    """


class ReportNotFoundError(ReportingError):
    pass


class TemplateError(ReportingError):
    pass


class RetrievalDenied(ReportingError):
    """Retrieval refused to return a record to this caller.

    Distinct from "not found". A caller who cannot see a record and a record that
    does not exist look the same from outside on purpose, but internally the
    difference is the one that matters when a report silently loses a citation.
    """
