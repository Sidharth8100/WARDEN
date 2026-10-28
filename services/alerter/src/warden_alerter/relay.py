"""Short DB claims, fenced completion, per-endpoint breaker and immutable retry identity."""

import asyncio
import os
import secrets
import smtplib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from warden_observe.telemetry import LATENCY, OPERATIONS, span

from warden_alerter.signing import sign
from warden_db.models import AlertEndpoint, Delivery, DeliveryAttempt, Outbox
from warden_net.ssrf_check import SSRFBlockedError
from warden_net.webhook import HttpResult, safe_post


@dataclass(frozen=True)
class Claim:
    identity: uuid.UUID
    token: uuid.UUID
    number: int
    kind: str
    destination: str = field(repr=False)
    body: bytes = field(repr=False)
    secret_ref: str | None
    key_id: str
    traceparent: str | None


@dataclass(frozen=True)
class Outcome:
    success: bool
    retryable: bool = False
    category: str | None = None
    http_status: int | None = None
    retry_after: float = 0


def environment_key(reference: str) -> bytes:
    if not reference.startswith("WARDEN_WEBHOOK_KEY_") or not all(
        c.isascii() and (c.isalnum() or c == "_") for c in reference
    ):
        raise ValueError("invalid_key_reference")
    value = os.environ.get(reference)
    if value is None or len(value.encode()) < 32:
        raise ValueError("operator_signing_key_unavailable")
    return value.encode()


def retry_after(value: str | None, now: datetime) -> float:
    if value is None:
        return 0
    try:
        seconds = (
            float(value)
            if value.isascii() and value.isdigit()
            else (parsedate_to_datetime(value) - now).total_seconds()
        )
        return max(0, min(300, seconds))
    except (ValueError, TypeError, OverflowError):
        return 0


def smtp_send(destination: str, body: bytes, identity: uuid.UUID) -> None:
    if "\r" in destination or "\n" in destination:
        raise ValueError("invalid_recipient")
    message = EmailMessage()
    message["From"] = "WARDEN <alerts@warden.invalid>"
    message["To"] = destination
    message["Subject"] = "WARDEN: monitored content changed"
    message["Message-ID"] = f"<{identity}@warden.invalid>"
    message.set_content(body.decode())
    # Operator-configured SMTP transport, never a user-supplied connection URL.
    with smtplib.SMTP(
        os.environ.get("WARDEN_SMTP_HOST", "mailpit"),
        int(os.environ.get("WARDEN_SMTP_PORT", "1025")),
        timeout=10,
    ) as client:
        client.send_message(message)


type Post = Callable[[str, bytes, dict[str, str]], Awaitable[HttpResult]]


class Relay:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        post: Post = safe_post,
        key_provider: Callable[[str], bytes] = environment_key,
        lease_s: float = 30,
    ):
        if not 0 < lease_s <= 300:
            raise ValueError("bounded lease required")
        self.sessions, self.post, self.key_provider, self.lease_s = (
            sessions,
            post,
            key_provider,
            lease_s,
        )

    async def claim(self, *, now: datetime | None = None) -> Claim | None:
        now = now or datetime.now(UTC)
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(Delivery)
                .join(Outbox)
                .where(
                    Outbox.kind.in_(("alert.webhook", "alert.email")),
                    Outbox.status == "pending",
                    Outbox.available_at <= now,
                    or_(
                        Delivery.status == "pending",
                        (Delivery.status == "leased") & (Delivery.lease_expires_at <= now),
                    ),
                )
                .order_by(Outbox.available_at, Delivery.id)
                .limit(1)
                .with_for_update(skip_locked=True, of=Delivery)
            )
            if row is None:
                return None
            outbox = await session.get(Outbox, row.outbox_id)
            endpoint = (
                await session.get(AlertEndpoint, row.endpoint_id, with_for_update=True)
                if row.endpoint_id
                else None
            )
            if outbox is None:
                raise RuntimeError("delivery_outbox_missing")
            if endpoint is None or not endpoint.enabled or endpoint.version != row.endpoint_version:
                row.status, row.last_error, outbox.status = (
                    "dead",
                    "destination_unavailable",
                    "failed",
                )
                return None
            if endpoint.open_until and endpoint.open_until > now:
                outbox.available_at = endpoint.open_until
                return None
            if endpoint.failure_count >= 5:
                if endpoint.probe_until and endpoint.probe_until > now:
                    outbox.available_at = endpoint.probe_until
                    return None
                endpoint.probe_until = now + timedelta(seconds=self.lease_s)
            if row.attempts >= row.retry_limit or now - row.created_at > timedelta(days=1):
                row.status, row.last_error, outbox.status = (
                    "dead",
                    "retry_policy_exhausted",
                    "failed",
                )
                return None
            await session.execute(
                update(DeliveryAttempt)
                .where(DeliveryAttempt.delivery_id == row.id, DeliveryAttempt.status == "claimed")
                .values(status="abandoned", error="lease_expired", finished_at=now)
            )
            row.attempts += 1
            row.lease_token, row.lease_expires_at = (
                uuid.uuid4(),
                now + timedelta(seconds=self.lease_s),
            )
            row.status = "leased"
            outbox.attempts += 1
            session.add(
                DeliveryAttempt(
                    delivery_id=row.id,
                    number=row.attempts,
                    token=row.lease_token,
                    status="claimed",
                    started_at=now,
                )
            )
            return Claim(
                row.id,
                row.lease_token,
                row.attempts,
                row.kind,
                row.destination,
                row.body.encode(),
                row.secret_ref,
                row.key_id,
                row.traceparent,
            )

    async def dispatch(self, claim: Claim) -> Outcome:
        started = time.monotonic()
        with span(
            "webhook.dispatch" if claim.kind == "webhook" else "email.dispatch", claim.traceparent
        ):
            try:
                if claim.kind == "email":
                    await asyncio.wait_for(
                        asyncio.to_thread(smtp_send, claim.destination, claim.body, claim.identity),
                        12,
                    )
                    return Outcome(True)
                if claim.secret_ref is None:
                    return Outcome(False, category="operator_signing_key_unavailable")
                key = self.key_provider(claim.secret_ref)
                timestamp = int(time.time())
                identity = str(claim.identity)
                result = await self.post(
                    claim.destination,
                    claim.body,
                    {
                        "Webhook-Id": identity,
                        "Webhook-Timestamp": str(timestamp),
                        "Webhook-Key-Id": claim.key_id,
                        "Webhook-Signature": sign(key, identity, timestamp, claim.body),
                    },
                )
                return Outcome(
                    200 <= result.status < 300,
                    result.status in (408, 429) or 500 <= result.status < 600,
                    None if 200 <= result.status < 300 else "http_rejected",
                    result.status,
                    retry_after(result.retry_after, datetime.now(UTC)),
                )
            except SSRFBlockedError:
                return Outcome(False, category="ssrf_denied")
            except (OSError, TimeoutError, smtplib.SMTPException):
                return Outcome(False, True, "transport_failure")
            except ValueError:
                return Outcome(False, category="invalid_configuration_or_response")
            finally:
                LATENCY.labels("alerter").observe(time.monotonic() - started)

    async def finish(self, claim: Claim, outcome: Outcome, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        async with self.sessions() as session, session.begin():
            row = await session.get(Delivery, claim.identity, with_for_update=True)
            if (
                row is None
                or row.status != "leased"
                or row.lease_token != claim.token
                or (row.lease_expires_at is None or row.lease_expires_at <= now)
            ):
                return False
            attempt = await session.scalar(
                select(DeliveryAttempt).where(
                    DeliveryAttempt.delivery_id == row.id, DeliveryAttempt.token == claim.token
                )
            )
            outbox = await session.get(Outbox, row.outbox_id)
            if outbox is None or attempt is None:
                raise RuntimeError("delivery_ledger_incomplete")
            endpoint = (
                await session.get(AlertEndpoint, row.endpoint_id, with_for_update=True)
                if row.endpoint_id
                else None
            )
            attempt.status, attempt.finished_at = "succeeded" if outcome.success else "failed", now
            attempt.http_status, attempt.error = outcome.http_status, outcome.category
            row.lease_token, row.lease_expires_at, row.last_error = None, None, outcome.category
            if endpoint:
                endpoint.probe_until = None
                if outcome.success:
                    endpoint.failure_count, endpoint.open_until = 0, None
                elif outcome.retryable:
                    endpoint.failure_count += 1
                    if endpoint.failure_count >= 5:
                        endpoint.open_until = now + timedelta(seconds=30)
            if outcome.success:
                row.status, row.delivered_at, outbox.status = "delivered", now, "sent"
            elif (
                outcome.retryable
                and row.attempts < row.retry_limit
                and now - row.created_at < timedelta(days=1)
            ):
                row.status = "pending"
                delay = (
                    min(300, 2 ** min(row.attempts, 12)) * secrets.randbelow(1_000_001) / 1_000_000
                )
                outbox.available_at = now + timedelta(seconds=max(delay, outcome.retry_after))
            else:
                row.status, outbox.status = "dead", "failed"
            OPERATIONS.labels(
                "alerter",
                "success" if outcome.success else "retry" if outcome.retryable else "dead",
            ).inc()
            return True

    async def once(self) -> bool:
        claim = await self.claim()
        if claim is None:
            return False
        outcome = await self.dispatch(claim)
        await self.finish(claim, outcome)
        return True
