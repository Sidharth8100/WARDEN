"""Reference ASGI receiver: authenticate first, receipt and effect in one SQLite tx."""

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from warden_alerter.signing import verify

type Receive = Callable[[], Awaitable[dict[str, object]]]
type Send = Callable[[dict[str, object]], Awaitable[None]]


class Receiver:
    def __init__(self, database: Path, keys: dict[str, bytes]):
        self.database, self.keys = database, keys
        if not keys or any(len(x) < 32 for x in keys.values()):
            raise ValueError("reference_receiver_requires_operator_keys")
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS receipts "
                "(id TEXT PRIMARY KEY, digest TEXT NOT NULL, received_at INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS effects "
                "(delivery_id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )

    def accept(self, headers: dict[str, str], body: bytes, *, now: int | None = None) -> int:
        identity = headers.get("webhook-id", "")
        key = self.keys.get(headers.get("webhook-key-id", ""))
        if key is None or not verify(
            key,
            identity,
            headers.get("webhook-timestamp", ""),
            body,
            headers.get("webhook-signature", ""),
            now=now or int(time.time()),
        ):
            return 401
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict) or payload.get("delivery_id") != identity:
                return 400
        except (ValueError, UnicodeError):
            return 400
        digest = hashlib.sha256(body).hexdigest()
        with sqlite3.connect(self.database, timeout=5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT digest FROM receipts WHERE id=?", (identity,)
            ).fetchone()
            if existing:
                return 200 if existing[0] == digest else 409
            connection.execute(
                "INSERT INTO receipts VALUES (?, ?, ?)", (identity, digest, now or int(time.time()))
            )
            connection.execute("INSERT INTO effects VALUES (?, ?)", (identity, body.decode()))
        return 200

    async def __call__(self, scope: dict[str, object], receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        body = bytearray()
        status = 405
        if scope.get("method") == "POST":
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(cast(bytes, message.get("body", b"")))
                if len(body) > 65536:
                    status = 413
                    break
                if not message.get("more_body"):
                    headers = {
                        key.decode().lower(): value.decode()
                        for key, value in cast(list[tuple[bytes, bytes]], scope["headers"])
                    }
                    status = self.accept(headers, bytes(body))
                    break
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def create_app() -> Receiver:
    key = os.environ.get("WARDEN_REFERENCE_KEY")
    if not key:
        raise RuntimeError("Human must provision WARDEN_REFERENCE_KEY; no key is generated")
    return Receiver(
        Path(os.environ.get("WARDEN_REFERENCE_DB", "/data/receiver.sqlite")), {"v1": key.encode()}
    )
