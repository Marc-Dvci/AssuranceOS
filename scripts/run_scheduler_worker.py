from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from assuranceos.config import settings
from assuranceos.db import Database
from assuranceos.db.models import AuditSchedule
from assuranceos.scheduling import AuditScheduler, PreflightContext


def active_tenants(database: Database) -> list[str]:
    """Return tenants with active schedules in stable order."""

    with database.read_session() as session:
        return list(
            session.scalars(
                select(AuditSchedule.tenant_id)
                .where(AuditSchedule.status == "active")
                .distinct()
                .order_by(AuditSchedule.tenant_id)
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate due AssuranceOS schedules and launch eligible engagements"
    )
    parser.add_argument(
        "--tenant-id",
        action="append",
        help="tenant to evaluate; repeat as needed (defaults to every tenant with an active schedule)",
    )
    parser.add_argument("--worker-id", default="cloud-run-scheduler")
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument(
        "--preflight-context-json",
        default="{}",
        help="JSON object matching PreflightContext; omitted values fail closed where required",
    )
    args = parser.parse_args()

    raw_context = json.loads(args.preflight_context_json)
    if not isinstance(raw_context, dict):
        raise SystemExit("--preflight-context-json must be a JSON object")
    context = PreflightContext.model_validate(raw_context)
    database = Database(settings.database_url)
    try:
        tenants = sorted(set(args.tenant_id or active_tenants(database)))
        scheduler = AuditScheduler(database)
        summaries = [
            scheduler.evaluate_due(
                tenant_id=tenant_id,
                context=context,
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
            ).model_dump(mode="json")
            for tenant_id in tenants
        ]
    finally:
        database.dispose()

    print(
        json.dumps(
            {
                "worker_id": args.worker_id,
                "tenant_count": len(tenants),
                "summaries": summaries,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
