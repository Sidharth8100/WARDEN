"""Root conftest.py: global fixtures and test marker auto-skip hooks."""

import asyncio
import os
import shutil
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from redis.asyncio import BlockingConnectionPool, Redis
from redis.asyncio.connection import UnixDomainSocketConnection
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.retry import Retry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from warden_db.session import make_engine, make_sessionmaker
from warden_redis.policy import ACL_SELECTORS


def _is_url_reachable(url: str, timeout: float = 0.5) -> bool:
    """Check if an HTTP endpoint responds with a 2xx status code."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except Exception:
        return False


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Auto-skip tests tagged with infrastructure markers if unreachable."""
    if item.get_closest_marker("selenium") and not _is_url_reachable(
        "http://127.0.0.1:4444/status"
    ):
        pytest.skip("Selenium Grid hub on 127.0.0.1:4444 is unreachable")
    if item.get_closest_marker("chaos_web") and not _is_url_reachable(
        "http://127.0.0.1:8000/health"
    ):
        pytest.skip("chaos-web service is unreachable")


@pytest.fixture
async def pg_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    binaries = Path("/usr/lib/postgresql/16/bin")
    if not await asyncio.to_thread(binaries.is_dir):
        pytest.skip("PostgreSQL 16 binaries required for private integration fixture")
    data = tmp_path / "data"
    socket = tmp_path / "socket"
    await asyncio.to_thread(socket.mkdir)
    init = await asyncio.create_subprocess_exec(
        str(binaries / "initdb"),
        "-D",
        str(data),
        "-A",
        "trust",
        "-U",
        "test",
        "--no-locale",
        "--encoding=UTF8",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, errors = await init.communicate()
    if init.returncode:
        pytest.fail(f"private initdb failed: {errors.decode()}")
    server = await asyncio.create_subprocess_exec(
        str(binaries / "postgres"),
        "-D",
        str(data),
        "-k",
        str(socket),
        "-c",
        "listen_addresses=",
        "-p",
        "15432",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    url = f"postgresql+asyncpg://test@/postgres?host={socket}&port=15432"
    engine = make_engine(url)
    try:
        for _ in range(100):
            if server.returncode is not None:
                pytest.fail("private PostgreSQL exited during startup")
            try:
                async with engine.connect():
                    break
            except Exception:
                await asyncio.sleep(0.03)
        else:
            pytest.fail("private PostgreSQL did not start")
        # Exercise the actual migration chain, including the new fence seed.
        executable = shutil.which("alembic")
        if executable is None:
            pytest.fail("alembic missing from the declared environment")
        migration = await asyncio.create_subprocess_exec(
            executable,
            "-c",
            "libs/warden_db/alembic.ini",
            "upgrade",
            "head",
            env={**os.environ, "WARDEN_DATABASE_URL": url},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await migration.communicate()
        if migration.returncode:
            pytest.fail(f"migration failed: {output.decode()}")
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()
        if server.returncode is None:
            server.terminate()
        await server.wait()
        await asyncio.to_thread(shutil.rmtree, data)


@pytest.fixture
async def private_redis_roles(tmp_path: Path) -> AsyncIterator[dict[str, Redis]]:
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.skip("redis-server required for private Unix-socket tests")
    await asyncio.to_thread(tmp_path.chmod, 0o700)
    socket = tmp_path / "redis.sock"
    config = tmp_path / "redis.conf"
    lines = [
        "port 0",
        f"unixsocket {socket}",
        "unixsocketperm 700",
        'save ""',
        "appendonly no",
        "user default off",
        "user testadmin on nopass +@all ~* &*",
    ]
    lines.extend(
        f"user {role} reset on nopass -@all +ping +hello +client|setname {' '.join(selectors)}"
        for role, selectors in ACL_SELECTORS.items()
    )
    config.write_text("\n".join(lines) + "\n")
    process = await asyncio.create_subprocess_exec(
        executable,
        str(config),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    clients = {
        role: Redis(
            connection_pool=BlockingConnectionPool(
                connection_class=UnixDomainSocketConnection,
                path=str(socket),
                username=role,
                password="",
                decode_responses=False,
                socket_connect_timeout=1,
                socket_timeout=1,
                retry=Retry(NoBackoff(), 0),
                max_connections=64,
                timeout=5,
            )
        )
        for role in [*ACL_SELECTORS, "testadmin"]
    }
    try:
        for _ in range(100):
            if process.returncode is not None:
                pytest.fail("private Redis failed to start (check Unix socket permissions)")
            try:
                await clients["testadmin"].ping()
                break
            except (OSError, RedisConnectionError):
                await asyncio.sleep(0.02)
        else:
            pytest.fail("private Redis did not become ready")
        yield clients
    finally:
        for client in clients.values():
            await client.aclose()
        if process.returncode is None:
            process.terminate()
        await process.wait()
