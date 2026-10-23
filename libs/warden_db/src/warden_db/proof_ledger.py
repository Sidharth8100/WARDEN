"""Operator-only Gate 2 ledger CLI; uses the existing trusted container identity.

No network endpoint is exposed. Docker-exec access is required. Only proof monitors
are accepted. Returns job IDs, statuses and counts; database credentials never leave
the trusted process or get printed. The untrusted fetcher never imports this module.
"""

import argparse
import asyncio
import json
import os
import sys
import uuid

from sqlalchemy import func, select

from warden_db.dispatch import stage_check
from warden_db.models import ChangeEvent, CrawlJob, Monitor, Outbox, Snapshot
from warden_db.session import make_engine, make_sessionmaker


async def query(
    action: str, monitor_id: uuid.UUID, job_ids: list[uuid.UUID], count: int
) -> dict[str, object]:
    engine = make_engine(os.environ["WARDEN_DATABASE_URL"])
    sessions = make_sessionmaker(engine)
    try:
        async with sessions() as session, session.begin():
            monitor = await session.get(Monitor, monitor_id, with_for_update=action == "stage")
            if monitor is None or not monitor.url.startswith(
                "http://chaos-web:8000/pages/price/gate2-"
            ):
                raise ValueError("only_gate2_simulator_monitors_allowed")
            if action == "stage":
                if not 1 <= count <= 2000:
                    raise ValueError("proof_batch_must_be_between_1_and_2000")
                jobs = [stage_check(session, monitor) for _ in range(count)]
                return {"job_ids": [job.job_id for job in jobs]}
            if action == "status":
                rows = (
                    await session.scalars(
                        select(CrawlJob).where(
                            CrawlJob.monitor_id == monitor_id, CrawlJob.id.in_(job_ids)
                        )
                    )
                ).all()
                return {
                    "receipts": [
                        {
                            "job_id": str(row.id),
                            "status": row.status,
                            "snapshot_id": row.snapshot_id,
                            "event_id": row.event_id,
                        }
                        for row in rows
                    ]
                }
            if action == "audit":
                actual = await session.scalar(
                    select(func.count())
                    .select_from(Snapshot)
                    .where(Snapshot.monitor_id == monitor_id)
                )
                effects = await session.scalar(
                    select(func.count())
                    .select_from(CrawlJob)
                    .where(CrawlJob.monitor_id == monitor_id, CrawlJob.snapshot_id.is_not(None))
                )
                events = await session.scalar(
                    select(func.count())
                    .select_from(ChangeEvent)
                    .where(ChangeEvent.monitor_id == monitor_id)
                )
                event_receipts = await session.scalar(
                    select(func.count())
                    .select_from(CrawlJob)
                    .where(CrawlJob.monitor_id == monitor_id, CrawlJob.event_id.is_not(None))
                )
                event_outboxes = await session.scalar(
                    select(func.count())
                    .select_from(Outbox)
                    .where(
                        Outbox.kind == "event",
                        Outbox.payload["monitor_id"].as_string() == str(monitor_id),
                    )
                )
                return {
                    "actual_snapshots": actual,
                    "receipt_snapshots": effects,
                    "actual_events": events,
                    "receipt_events": event_receipts,
                    "event_outboxes": event_outboxes,
                }
            raise ValueError("unknown_proof_action")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "status", "audit"))
    parser.add_argument("monitor_id", type=uuid.UUID)
    parser.add_argument("--job-ids", default="[]")
    parser.add_argument("--count", type=int, default=500)
    args = parser.parse_args()
    try:
        ids = [uuid.UUID(identity) for identity in json.loads(args.job_ids)]
        print(
            json.dumps(
                asyncio.run(
                    asyncio.wait_for(
                        query(args.action, args.monitor_id, ids, args.count),
                        timeout=15,
                    )
                )
            )
        )
    except Exception as exc:
        # Even malformed operator input must not print credential-bearing errors.
        print(type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
