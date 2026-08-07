"""Canonical reads and writes for standards, criteria, packs, and compilations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from assuranceos.db.models import (
    AuditPackRegistration,
    CriteriaCrosswalk,
    CriteriaMapping,
    Criterion,
    PackCompilation,
    Standard,
    StandardEntitlement,
)


class StandardsRepository:
    def __init__(self, session: Session):
        self.session = session

    # -- standards and criteria ------------------------------------------------

    def add_standard(self, standard: Standard) -> Standard:
        self.session.add(standard)
        self.session.flush()
        return standard

    def get_standard(self, code: str, version: str) -> Standard | None:
        return self.session.scalar(
            select(Standard).where(Standard.code == code, Standard.version == version)
        )

    def latest_standard(self, code: str) -> Standard | None:
        """The most recently created version of a standard.

        Ordered by creation rather than by parsing the version string. Standards
        bodies version by year, by dotted number, and by letter; creation order is
        the one fact that holds across all of them.
        """
        return self.session.scalar(
            select(Standard)
            .where(Standard.code == code)
            .order_by(Standard.created_at.desc(), Standard.standard_id.desc())
        )

    def list_standards(self) -> list[Standard]:
        return list(
            self.session.scalars(select(Standard).order_by(Standard.code, Standard.version))
        )

    def add_criterion(self, criterion: Criterion) -> Criterion:
        self.session.add(criterion)
        self.session.flush()
        return criterion

    def get_criterion(self, standard_id: str, code: str) -> Criterion | None:
        return self.session.scalar(
            select(Criterion).where(
                Criterion.standard_id == standard_id, Criterion.code == code
            )
        )

    def criteria_for(self, standard_id: str) -> list[Criterion]:
        return list(
            self.session.scalars(
                select(Criterion)
                .where(Criterion.standard_id == standard_id)
                .order_by(Criterion.code)
            )
        )

    # -- crosswalks and mappings -----------------------------------------------

    def add_crosswalk(self, crosswalk: CriteriaCrosswalk) -> CriteriaCrosswalk:
        self.session.add(crosswalk)
        self.session.flush()
        return crosswalk

    def crosswalks_from(self, criterion_id: str) -> list[CriteriaCrosswalk]:
        return list(
            self.session.scalars(
                select(CriteriaCrosswalk)
                .where(CriteriaCrosswalk.source_criterion_id == criterion_id)
                .order_by(CriteriaCrosswalk.relation, CriteriaCrosswalk.target_criterion_id)
            )
        )

    def crosswalks_touching(self, criterion_id: str) -> list[CriteriaCrosswalk]:
        """Every crosswalk with this criterion at either end.

        Change impact runs in both directions: a criterion that is the *target* of
        an equivalence is affected when its source is revised just as much as the
        other way round.
        """
        return list(
            self.session.scalars(
                select(CriteriaCrosswalk)
                .where(
                    (CriteriaCrosswalk.source_criterion_id == criterion_id)
                    | (CriteriaCrosswalk.target_criterion_id == criterion_id)
                )
                .order_by(CriteriaCrosswalk.crosswalk_id)
            )
        )

    def add_mapping(self, mapping: CriteriaMapping) -> CriteriaMapping:
        self.session.add(mapping)
        self.session.flush()
        return mapping

    def mappings_for(self, criterion_id: str) -> list[CriteriaMapping]:
        return list(
            self.session.scalars(
                select(CriteriaMapping)
                .where(CriteriaMapping.criterion_id == criterion_id)
                .order_by(CriteriaMapping.target_type, CriteriaMapping.target_ref)
            )
        )

    # -- entitlements ----------------------------------------------------------

    def add_entitlement(self, entitlement: StandardEntitlement) -> StandardEntitlement:
        self.session.add(entitlement)
        self.session.flush()
        return entitlement

    def entitlements(self, tenant_id: str) -> list[StandardEntitlement]:
        return list(
            self.session.scalars(
                select(StandardEntitlement)
                .where(
                    StandardEntitlement.tenant_id == tenant_id,
                    StandardEntitlement.revoked_at.is_(None),
                )
                .order_by(StandardEntitlement.standard_code)
            )
        )

    # -- pack registrations ----------------------------------------------------

    def add_registration(self, registration: AuditPackRegistration) -> AuditPackRegistration:
        self.session.add(registration)
        self.session.flush()
        return registration

    def get_registration(self, pack_id: str, version: str) -> AuditPackRegistration | None:
        return self.session.scalar(
            select(AuditPackRegistration).where(
                AuditPackRegistration.pack_id == pack_id,
                AuditPackRegistration.version == version,
            )
        )

    def registration_by_id(self, registration_id: str) -> AuditPackRegistration | None:
        return self.session.get(AuditPackRegistration, registration_id)

    def list_registrations(self) -> list[AuditPackRegistration]:
        return list(
            self.session.scalars(
                select(AuditPackRegistration).order_by(
                    AuditPackRegistration.pack_id, AuditPackRegistration.version
                )
            )
        )

    # -- compilations ----------------------------------------------------------

    def add_compilation(self, compilation: PackCompilation) -> PackCompilation:
        self.session.add(compilation)
        self.session.flush()
        return compilation

    def compilation_for(self, tenant_id: str, engagement_id: str) -> PackCompilation | None:
        return self.session.scalar(
            select(PackCompilation).where(
                PackCompilation.tenant_id == tenant_id,
                PackCompilation.engagement_id == engagement_id,
            )
        )

    def compilations_of(self, pack_id: str) -> list[PackCompilation]:
        return list(
            self.session.scalars(
                select(PackCompilation)
                .where(PackCompilation.pack_id == pack_id)
                .order_by(PackCompilation.compiled_at)
            )
        )
