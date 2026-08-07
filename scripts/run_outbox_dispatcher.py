from __future__ import annotations

import argparse
import json

from assuranceos.config import settings
from assuranceos.db.session import Database
from assuranceos.outbox import GooglePubSubPublisher, InMemoryPublisher, OutboxDispatcher


def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch AssuranceOS transactional outbox events")
    parser.add_argument("--worker-id", default="local-outbox")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--project-id")
    parser.add_argument("--topic-id")
    args = parser.parse_args()
    database = Database(settings.database_url)
    publisher = (
        GooglePubSubPublisher(project_id=args.project_id, topic_id=args.topic_id)
        if args.project_id and args.topic_id
        else InMemoryPublisher()
    )
    report = OutboxDispatcher(database, publisher).dispatch_once(
        worker_id=args.worker_id, limit=args.limit
    )
    print(json.dumps(report.__dict__, default=lambda value: value.__dict__, indent=2))


if __name__ == "__main__":
    main()
