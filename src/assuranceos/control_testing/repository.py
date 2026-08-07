from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from assuranceos.db.models import (
    ControlTestDatasetBinding,
    ControlTestException,
    ControlTestRelease,
    ControlTestRun,
)


class ControlTestRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_release(self, test_id: str, version: str) -> ControlTestRelease | None:
        return self.session.scalar(
            select(ControlTestRelease).where(
                ControlTestRelease.test_id == test_id,
                ControlTestRelease.version == version,
                ControlTestRelease.release_status == "released",
            )
        )

    def list_releases(self, domain: str | None = None) -> list[ControlTestRelease]:
        statement = select(ControlTestRelease).where(
            ControlTestRelease.release_status == "released"
        )
        if domain:
            statement = statement.where(ControlTestRelease.domain == domain)
        return list(self.session.scalars(statement.order_by(ControlTestRelease.test_id, ControlTestRelease.version)))

    def get_run(self, tenant_id: str, run_id: str) -> ControlTestRun | None:
        return self.session.scalar(
            select(ControlTestRun).where(
                ControlTestRun.tenant_id == tenant_id,
                ControlTestRun.run_id == run_id,
            )
        )

    def get_run_by_idempotency(self, tenant_id: str, key: str) -> ControlTestRun | None:
        return self.session.scalar(
            select(ControlTestRun).where(
                ControlTestRun.tenant_id == tenant_id,
                ControlTestRun.idempotency_key == key,
            )
        )

    def add_run(self, run: ControlTestRun) -> None:
        self.session.add(run)
        self.session.flush()

    def add_binding(self, binding: ControlTestDatasetBinding) -> None:
        self.session.add(binding)

    def add_exception(self, exception: ControlTestException) -> None:
        self.session.add(exception)

    def bindings(self, tenant_id: str, run_id: str) -> list[ControlTestDatasetBinding]:
        return list(
            self.session.scalars(
                select(ControlTestDatasetBinding)
                .where(
                    ControlTestDatasetBinding.tenant_id == tenant_id,
                    ControlTestDatasetBinding.run_id == run_id,
                )
                .order_by(ControlTestDatasetBinding.dataset_name)
            )
        )

    def exceptions(self, tenant_id: str, run_id: str) -> list[ControlTestException]:
        return list(
            self.session.scalars(
                select(ControlTestException)
                .where(
                    ControlTestException.tenant_id == tenant_id,
                    ControlTestException.run_id == run_id,
                )
                .order_by(ControlTestException.exception_key)
            )
        )
