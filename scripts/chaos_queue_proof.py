"""Gate 2 LIVE proof. Requires human provisioning; never reads credential files.

Run with environment values supplied by the operator/uv --env-file. Kills only
this Compose project's fetcher/processor. No deletes, volume operations, raw
container-env dumps, or credential-bearing log output. Restarts workers in finally.
"""

import argparse
import asyncio
import contextlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import func, select
from websockets.asyncio.client import connect as websocket_connect

from warden_db.dispatch import stage_check
from warden_db.models import CrawlJob, Monitor, Snapshot
from warden_db.session import make_engine, make_sessionmaker
from warden_redis.client import connect


async def compose(*arguments: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "docker",
        "compose",
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, _ = await process.communicate()
    if process.returncode:
        raise RuntimeError(
            f"Compose {arguments[0]} failed; inspect locally without sharing secrets"
        )
    return output.decode()


async def mutation(page: str) -> None:
    # This is the simulator's explicit test-only mutation endpoint, reached
    # internally without publishing another host port.
    source = (
        "import json,sys,urllib.request; "
        "url='http://localhost:8000/__mutate__/price/'+sys.argv[1]; "
        "r=urllib.request.Request(url,method='POST'); "
        "print(json.load(urllib.request.urlopen(r))['kind'])"
    )
    for _ in range(30):
        if (
            await compose("exec", "-T", "chaos-web", "python", "-c", source, page)
        ).strip() == "signal":
            return
    raise RuntimeError("simulator did not produce a signal mutation")


async def storage_boundary() -> None:
    """Exercise role credentials inside their existing containers, never expose them.

    The write-denial probe uses the exact already-written bytes/key: even a
    misconfigured successful write is idempotent and does not corrupt data.
    Only a genuine S3 authorization rejection counts, not a connection failure.
    """
    config = (
        "from warden_redis.worker import WorkerConfig; "
        "from warden_net.objectstore import ObjectStoreConfig; "
        "c=WorkerConfig.from_environment(); "
        "s=ObjectStoreConfig(c.objectstore_endpoint,c.objectstore_access_key,"
        "c.objectstore_secret_key,c.objectstore_bucket); "
    )
    digest = (
        await compose(
            "exec",
            "-T",
            "fetcher",
            "python",
            "-c",
            "import asyncio; from warden_net.objectstore import put_blob; "
            + config
            + "print(asyncio.run(put_blob(s,b'WARDEN storage permission probe')))",
        )
    ).strip()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise RuntimeError("fetcher storage write probe failed")
    source = (
        "import asyncio,sys\n"
        "from warden_net.objectstore import get_blob,put_blob\n"
        "from botocore.exceptions import ClientError\n" + config + "\n"
        "async def probe():\n"
        " data=await get_blob(s,sys.argv[1])\n"
        " assert data==b'WARDEN storage permission probe'\n"
        " try:\n"
        "  await put_blob(s,data)\n"
        " except ClientError as exc:\n"
        "  assert exc.response['ResponseMetadata']['HTTPStatusCode']==403\n"
        "  print('processor_read_allowed_write_denied')\n"
        " else:\n"
        "  raise RuntimeError('processor_write_was_allowed')\n"
        "asyncio.run(probe())\n"
    )
    result = await compose("exec", "-T", "processor", "python", "-c", source, digest)
    if result.strip() != "processor_read_allowed_write_denied":
        raise RuntimeError("processor storage permissions probe failed")


async def run(args: argparse.Namespace) -> dict[str, object]:
    engine = make_engine(os.environ["WARDEN_DATABASE_URL"])
    sessions = make_sessionmaker(engine)
    redis = connect(os.environ["WARDEN_REDIS_URL"], "scheduler")
    stop = asyncio.Event()
    kills: list[dict[str, object]] = []
    password = os.environ["WARDEN_PROOF_PASSWORD"]
    page = f"gate2-{uuid.uuid4().hex}"
    started = time.monotonic()

    async def wait_completed(ids: list[uuid.UUID]) -> list[CrawlJob]:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            async with sessions() as session:
                rows = list(
                    (await session.scalars(select(CrawlJob).where(CrawlJob.id.in_(ids)))).all()
                )
                if any(row.status == "failed" for row in rows):
                    raise RuntimeError("accepted job terminated in failure; Gate 2 did not pass")
                if len(rows) == len(ids) and all(row.status == "completed" for row in rows):
                    return rows
            await asyncio.sleep(0.25)
        raise TimeoutError("some accepted jobs remained unfinished at the proof deadline")

    async def chaos() -> None:
        for index in range(args.kills if args.kill_workers else 0):
            if stop.is_set():
                break
            await asyncio.sleep(0.4 + secrets.randbelow(1200) / 1000)
            service = (
                ("fetcher", "processor")[index]
                if index < 2
                else secrets.choice(("fetcher", "processor"))
            )
            await compose("kill", "--signal", "SIGKILL", service)
            kills.append({"service": service, "at_s": time.monotonic() - started})
            await asyncio.sleep(0.15)
            await compose("start", service)

    task: asyncio.Task[None] | None = None
    try:
        isolation = await compose(
            "exec",
            "-T",
            "fetcher",
            "python",
            "-c",
            "import importlib.util,os; "
            "assert 'WARDEN_DATABASE_URL' not in os.environ; "
            "packages=('sqlalchemy','asyncpg','warden_db'); "
            "assert all(importlib.util.find_spec(p) is None for p in packages); "
            "print('fetcher_isolated')",
        )
        if isolation.strip() != "fetcher_isolated":
            raise RuntimeError("fetcher isolation proof failed")
        await storage_boundary()
        async with httpx.AsyncClient(base_url=args.api, timeout=30) as api:
            email = f"{page}@example.com"
            response = await api.post(
                "/api/auth/register", json={"email": email, "password": password}
            )
            response.raise_for_status()
            response = await api.post(
                "/api/auth/login", json={"email": email, "password": password}
            )
            response.raise_for_status()
            token = response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            response = await api.post(
                "/api/monitors",
                headers=headers,
                json={"url": f"http://chaos-web:8000/pages/price/{page}"},
            )
            response.raise_for_status()
            monitor_id = uuid.UUID(response.json()["id"])
            baseline = await api.post(f"/api/monitors/{monitor_id}/check", headers=headers)
            baseline.raise_for_status()
            baseline_id = uuid.UUID(baseline.json()["job_id"])
            await wait_completed([baseline_id])
            # Connect to api2; submit the meaningful check through api1.
            async with websocket_connect(args.websocket, open_timeout=10) as socket:
                await socket.send(
                    json.dumps({"token": token, "monitor_ids": [str(monitor_id)], "cursor": "0-0"})
                )
                while (
                    json.loads(await asyncio.wait_for(socket.recv(), 10)).get("type")
                    != "subscribed"
                ):
                    pass
                await mutation(page)
                begin_live = time.monotonic()
                check = await api.post(f"/api/monitors/{monitor_id}/check", headers=headers)
                check.raise_for_status()
                await wait_completed([uuid.UUID(check.json()["job_id"])])
                event = None
                for _ in range(100):
                    message = json.loads(await asyncio.wait_for(socket.recv(), 10))
                    if message.get("type") == "change" and message["monitor_id"] == str(monitor_id):
                        event = message
                        break
                if event is None:
                    raise RuntimeError("api2 did not receive api1's change")
                live_seconds = time.monotonic() - begin_live
                # A publish retry must not duplicate the same delivered event.
                try:
                    while True:
                        repeated = json.loads(await asyncio.wait_for(socket.recv(), 1.2))
                        if (
                            repeated.get("type") == "change"
                            and repeated.get("event_id") == event["event_id"]
                        ):
                            raise RuntimeError("duplicate WebSocket delivery")
                except TimeoutError:
                    pass
            before_reclaims = int(await redis.get("warden:metrics:reclaimed") or 0)
            before_retries = int(await redis.get("warden:metrics:retries") or 0)
            async with sessions() as session, session.begin():
                monitor = await session.get(Monitor, monitor_id, with_for_update=True)
                if monitor is None:
                    raise RuntimeError("proof monitor missing")
                jobs = [stage_check(session, monitor) for _ in range(args.jobs)]
            task = asyncio.create_task(chaos())
            ids = [uuid.UUID(job.job_id) for job in jobs]
            rows = await wait_completed(ids)
            stop.set()
            await task
            task = None
            snapshots = [row.snapshot_id for row in rows]
            if any(identity is None for identity in snapshots) or len(set(snapshots)) != len(ids):
                raise RuntimeError("missing or duplicate per-job snapshot effects")
            async with sessions() as session:
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
                if actual != effects:
                    raise RuntimeError("snapshot without exactly one durable job receipt")
            reclaimed = int(await redis.get("warden:metrics:reclaimed") or 0) - before_reclaims
            retries = int(await redis.get("warden:metrics:retries") or 0) - before_retries
            if args.kill_workers and (len(kills) < 2 or reclaimed < 1):
                raise RuntimeError(
                    "run did not exercise worker reclaim; rerun with more jobs/kills"
                )
            return {
                "jobs": args.jobs,
                "completed": len(rows),
                "lost": 0,
                "duplicate_durable_effects": 0,
                "reclaimed": reclaimed,
                "retries_fired": retries,
                "elapsed_s": time.monotonic() - started,
                "kills": kills,
                "cross_replica_event_id": event["event_id"],
                "cross_replica_end_to_end_s": live_seconds,
                "fetcher_database_packages_absent": True,
                "processor_storage_read_allowed_write_denied": True,
            }
    finally:
        stop.set()
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        try:
            if args.kill_workers:
                await compose("start", "fetcher", "processor")
        finally:
            await redis.aclose()
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=500)
    parser.add_argument("--kills", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--kill-workers", action="store_true")
    parser.add_argument("--api", default="http://127.0.0.1:18000")
    parser.add_argument("--websocket", default="ws://127.0.0.1:18001/api/ws")
    parser.add_argument("--output", type=Path, default=Path("docs/reports/part-1-gate2.json"))
    args = parser.parse_args()
    if args.jobs < 1 or args.kills < 2 or args.timeout <= 0:
        parser.error("positive jobs/timeout and at least two kill attempts required")
    result = asyncio.run(run(args))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
