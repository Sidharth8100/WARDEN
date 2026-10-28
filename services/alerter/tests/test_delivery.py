"""Authentication, persistent receiver dedup and replay boundary tests."""

import json
import uuid
from pathlib import Path

import pytest
from warden_alerter.receiver import Receiver
from warden_alerter.signing import sign, verify

KEY = b"fixture-only-not-a-deployment-key-123456"


def test_signature_exact_bytes_freshness_and_rotation(tmp_path: Path) -> None:
    identity = str(uuid.uuid4())
    body = json.dumps({"delivery_id": identity}).encode()
    signature = sign(KEY, identity, 1000, body)
    assert verify(KEY, identity, "1000", body, signature, now=1300)
    assert not verify(KEY, identity, "1000", body, signature, now=1301)
    assert not verify(KEY, identity, "1000", body + b" ", signature, now=1000)
    assert not verify(KEY, str(uuid.uuid4()), "1000", body, signature, now=1000)
    assert not verify(KEY, identity, "NaN", body, signature, now=1000)
    receiver = Receiver(tmp_path / "receiver.sqlite", {"current": KEY, "previous": KEY + b"old"})
    headers = {
        "webhook-id": identity,
        "webhook-key-id": "current",
        "webhook-timestamp": "1000",
        "webhook-signature": signature,
    }
    assert receiver.accept(headers, body, now=1000) == 200
    assert receiver.accept(headers, body, now=1000) == 200
    restored = Receiver(tmp_path / "receiver.sqlite", {"current": KEY})
    assert restored.accept(headers, body, now=1000) == 200
    import sqlite3

    with sqlite3.connect(tmp_path / "receiver.sqlite") as db:
        assert db.execute("SELECT count(*) FROM effects").fetchone()[0] == 1
    changed = json.dumps({"delivery_id": identity, "changed": True}).encode()
    headers["webhook-signature"] = sign(KEY, identity, 1000, changed)
    assert restored.accept(headers, changed, now=1000) == 409
    headers["webhook-key-id"] = "unknown"
    assert restored.accept(headers, changed, now=1000) == 401


@pytest.mark.parametrize("timestamp", ["-1", "1.2", " 1000", "1" * 500, "\uff11\uff12\uff13"])
def test_malformed_timestamps(timestamp: str) -> None:
    assert not verify(KEY, str(uuid.uuid4()), timestamp, b"{}", "v1=" + "0" * 64, now=1000)
