class EvidenceVaultError(RuntimeError):
    """Base exception for stable evidence-vault failures."""


class EvidenceNotFoundError(EvidenceVaultError):
    pass


class EvidenceDeletedError(EvidenceVaultError):
    pass


class AcquisitionConflictError(EvidenceVaultError):
    pass


class ObjectNotFoundError(EvidenceVaultError):
    pass


class ObjectIntegrityError(EvidenceVaultError):
    pass


class ImmutableObjectConflictError(EvidenceVaultError):
    pass


class RetentionPolicyError(EvidenceVaultError):
    pass


class CustodyChainError(EvidenceVaultError):
    pass


class ExportPackageError(EvidenceVaultError):
    pass
