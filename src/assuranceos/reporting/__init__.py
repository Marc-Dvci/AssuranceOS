"""Access-aware retrieval, the claim graph, and evidence-grounded reporting.

The back of the loop. One rule carries it: a material claim either resolves to
admissible evidence, or it carries a stated limitation, or the report does not
render.
"""

from .definitions import (
    ClaimInput,
    ClaimIssue,
    ClaimType,
    EvidencePolicy,
    EvidenceRelationship,
    EvidenceView,
    RenderedClaim,
    RenderedReport,
    ReportRequest,
    ReportSection,
    ReportTemplate,
    ReportType,
    ReuseJustification,
)
from .exceptions import (
    ReportingError,
    ReportNotFoundError,
    RetrievalDenied,
    TemplateError,
    UnsupportedClaimError,
)
from .renderer import (
    canonical_bytes,
    check_claim,
    document_digest,
    render,
    require_rendered,
)
from .service import (
    DEFAULT_VISIBLE_CLASSIFICATIONS,
    ReportingService,
    claim_from_finding,
)

__all__ = [
    "DEFAULT_VISIBLE_CLASSIFICATIONS",
    "ClaimInput",
    "ClaimIssue",
    "ClaimType",
    "EvidencePolicy",
    "EvidenceRelationship",
    "EvidenceView",
    "RenderedClaim",
    "RenderedReport",
    "ReportNotFoundError",
    "ReportRequest",
    "ReportSection",
    "ReportTemplate",
    "ReportType",
    "ReportingError",
    "ReportingService",
    "RetrievalDenied",
    "ReuseJustification",
    "TemplateError",
    "UnsupportedClaimError",
    "canonical_bytes",
    "check_claim",
    "claim_from_finding",
    "document_digest",
    "render",
    "require_rendered",
]
