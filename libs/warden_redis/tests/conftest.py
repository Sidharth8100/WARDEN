"""Real Redis on a private Unix socket, no TCP, no secrets, no user data.

Test identities use nopass ONLY inside the fixture-owned 0700 temporary directory.
This verifies actual scripts and ACL semantics without provisioning deployment
credentials. Deployment must use human-provisioned password hashes instead.
"""

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from redis.asyncio import BlockingConnectionPool, Redis
from redis.asyncio.connection import UnixDomainSocketConnection
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.retry import Retry

from warden_redis.policy import ACL_SELECTORS


@pytest.fixture
async def redis_roles(tmp_path: Path) -> AsyncIterator[dict[str, Redis]]:
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
