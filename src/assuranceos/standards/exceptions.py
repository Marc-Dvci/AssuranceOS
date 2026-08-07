"""Refusals from the standards service and the Audit Pack compiler.

Each names a distinct reason a pack cannot become an engagement. The distinction
matters operationally: an unsigned pack is a supply-chain event, an unentitled one
is a licensing event, and an out-of-period criterion is a methodology error. A
caller that can only see "compilation failed" cannot route any of them.
"""

from __future__ import annotations


class StandardsError(Exception):
    """Base class for every standards and Audit Pack refusal."""


class StandardNotFoundError(StandardsError):
    pass


class CriterionNotFoundError(StandardsError):
    pass


class PackNotFoundError(StandardsError):
    pass


class PackSignatureError(StandardsError):
    """The pack is unsigned, mis-signed, or its files changed after release.

    Treated as distinct from a schema error because it is not a mistake in the
    pack — it is either a key problem or a modified artefact, and both are
    supply-chain conditions rather than authoring ones.
    """


class PackSchemaError(StandardsError):
    """The pack does not satisfy the Audit Pack schema or its own coherence rules."""


class PackNotReleasedError(StandardsError):
    """The pack exists but is a draft, in review, or retired."""


class PackCompatibilityError(StandardsError):
    """The platform cannot satisfy what the pack requires.

    A missing control-test release, an agent role that is not in the registry, or
    a platform older than the pack's floor. Raised at compile time, where the
    message can name the pack and the artefact, rather than at run time where it
    would surface as a task failure.
    """


class PackEntitlementError(StandardsError):
    """The tenant is not entitled to the standard this pack reproduces.

    Licensed criteria text is not a detail. Compiling a pack that quotes a
    standard the tenant has no licence for creates a legal exposure the platform
    would be the author of, so it fails closed.
    """


class CriteriaEffectivityError(StandardsError):
    """A criterion does not cover the audit period it would be applied to.

    Partial coverage counts as failure. A rule that came into force halfway
    through the period cannot support a conclusion about the whole of it.
    """


class PackCompilationError(StandardsError):
    """The pack is valid but its graph cannot be compiled for this engagement."""


class DuplicateStandardError(StandardsError):
    pass
