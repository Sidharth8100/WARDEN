"""LISTEN before scan, reconnect/rescan, polling repair; dedicated session connection."""

import asyncio
import contextlib
import os

from sqlalchemy import text
from warden_observe.telemetry import OPERATIONS, configure, flush

from warden_alerter.relay import Relay
from warden_db.session import make_engine, make_sessionmaker
from warden_redis.worker import shutdown_event, wait_or_stop


async def run() -> None:
    configure("warden-alerter")
    # This direct session connection must not go through transaction-pooling PgBouncer.
    engine = make_engine(os.environ["WARDEN_DATABASE_URL"])
    relay = Relay(make_sessionmaker(engine))
    stop, wake = shutdown_event(), asyncio.Event()

    def notify(connection: object, pid: int, channel: str, payload: str) -> None:
        wake.set()

    try:
        while not stop.is_set():
            try:
                async with engine.connect() as connection:
                    raw = await connection.get_raw_connection()
                    driver = raw.driver_connection
                    if driver is None:
                        raise RuntimeError("listener_driver_unavailable")
                    # asyncpg callback is intentionally tiny; work stays outside it.
                    await driver.add_listener("warden_alerts", notify)
                    await connection.execute(text("SELECT 1"))
                    await connection.commit()
                    try:
                        while not stop.is_set():
                            wake.clear()
                            did_work = await relay.once()
                            if not did_work:
                                with contextlib.suppress(TimeoutError):
                                    await asyncio.wait_for(wake.wait(), 1)
                    finally:
                        await driver.remove_listener("warden_alerts", notify)
            except Exception:
                OPERATIONS.labels("alerter_listener", "error").inc()
                await wait_or_stop(stop, 1)
    finally:
        await engine.dispose()
        flush()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
