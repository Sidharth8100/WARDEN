"""Named identities, byte-safe decoding and per-client registered scripts."""

import re
from collections.abc import Sequence
from urllib.parse import unquote, urlsplit
from weakref import WeakKeyDictionary

from redis.asyncio import BlockingConnectionPool, Redis
from redis.commands.core import AsyncScript

_scripts: WeakKeyDictionary[Redis, dict[str, AsyncScript]] = WeakKeyDictionary()


def connect(url: str, identity: str) -> Redis:
    """Reject implicit/default users and unexpected identities before connecting.

    Never include a URL or Redis exception text in an application log: both may
    contain credentials. Binary responses preserve arbitrary cached bytes.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in ("redis", "rediss") or not parsed.password:
        raise ValueError("authenticated Redis URL required")
    if unquote(parsed.username or "") != identity or identity == "default":
        raise ValueError("Redis identity does not match service role")
    pool = BlockingConnectionPool.from_url(
        url,
        decode_responses=False,
        socket_connect_timeout=3,
        socket_timeout=10,
        health_check_interval=15,
        max_connections=64,
        timeout=5,
    )
    return Redis(connection_pool=pool)


def component(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", value):
        raise ValueError("invalid Redis namespace component")
    return value


def as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode()
    if isinstance(value, str):
        return value
    raise TypeError("expected Redis string response")


def as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    raise TypeError("expected Redis bytes response")


async def run_script(
    redis: Redis, source: str, keys: Sequence[str], args: Sequence[str | bytes | int | float]
) -> object:
    registered = _scripts.setdefault(redis, {})
    script = registered.get(source)
    if script is None:
        script = redis.register_script(source)
        registered[source] = script
    result: object = await script(keys=list(keys), args=list(args), client=redis)
    return result
