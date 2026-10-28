"""Operator rollback: immutable versions stay recorded; only the owned alias moves."""

import argparse
import asyncio
import os
import uuid

from warden_db.learning import rollback
from warden_db.models import AuditLog
from warden_db.session import make_engine, make_sessionmaker


async def run(owner: uuid.UUID) -> None:
    engine = make_engine(os.environ["WARDEN_DATABASE_URL"])
    try:
        async with make_sessionmaker(engine)() as session, session.begin():
            await rollback(session, owner)
            session.add(AuditLog(user_id=owner, action="model.rollback.cli", resource_type="model"))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("owner", type=uuid.UUID)
    args = parser.parse_args()
    asyncio.run(run(args.owner))
    print("Rolled back the selected owner's production model.")
