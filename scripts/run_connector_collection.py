from __future__ import annotations

import argparse
import json
from uuid import uuid4

from assuranceos.config import settings
from assuranceos.connectors import CollectionRequest, ConnectorService, CredentialResolver
from assuranceos.connectors.factory import ConnectorFactory
from assuranceos.db import Database
from assuranceos.vault import BaselineContentInspector, Ed25519ManifestSigner, EvidenceVault, GoogleCloudStorageObjectStore


def _json_object(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one governed connector collection")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--connector-instance-id", required=True)
    parser.add_argument("--grant-id", required=True)
    parser.add_argument("--stream", required=True)
    parser.add_argument("--scope", type=_json_object, default={})
    parser.add_argument("--parameters", type=_json_object, default={})
    parser.add_argument("--engagement-id")
    parser.add_argument("--task-id")
    parser.add_argument("--classification", default="internal")
    parser.add_argument("--idempotency-key", default=None)
    args = parser.parse_args()

    database = Database(settings.database_url)
    signer = (
        Ed25519ManifestSigner.from_pem(
            settings.export_signing_private_key, key_id=settings.export_signing_key_id
        )
        if settings.export_signing_private_key
        else None
    )
    if settings.evidence_storage == "gcs":
        store = GoogleCloudStorageObjectStore(settings.evidence_bucket or "")
        vault = EvidenceVault(
            database, store, export_signer=signer, inspector=BaselineContentInspector()
        )
    else:
        vault = EvidenceVault.local(
            database,
            settings.evidence_root,
            export_signer=signer,
            inspector=BaselineContentInspector(),
        )
    service = ConnectorService(database, vault)
    instance = next(
        (
            item
            for item in service.list_instances(args.tenant_id)
            if item.connector_instance_id == args.connector_instance_id
        ),
        None,
    )
    if instance is None:
        raise SystemExit("connector instance not found")
    connector = ConnectorFactory(CredentialResolver()).build(instance)
    summary = service.run(
        tenant_id=args.tenant_id,
        connector_instance_id=args.connector_instance_id,
        grant_id=args.grant_id,
        connector=connector,
        request=CollectionRequest(
            stream=args.stream,
            scope=args.scope,
            parameters=args.parameters,
            engagement_id=args.engagement_id,
            task_id=args.task_id,
            classification=args.classification,
        ),
        idempotency_key=args.idempotency_key or f"manual:{uuid4().hex}",
    )
    print(summary.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
