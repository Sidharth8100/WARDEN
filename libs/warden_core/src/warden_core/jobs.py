"""Versioned, validated JSON contracts shared without importing any service."""

import json
import re
from dataclasses import asdict, dataclass, replace
from typing import cast
from uuid import UUID


@dataclass(frozen=True)
class JobPayload:
    job_id: str
    monitor_id: str
    url: str
    domain: str
    needs_render: bool = False
    attempt: int = 0
    content_hash: str | None = None
    screenshot_hash: str | None = None
    http_status: int = 0
    tier: int = 1
    fetched_at: str | None = None
    error: str | None = None
    not_modified: bool = False
    blocked_by_robots: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        UUID(self.job_id)
        UUID(self.monitor_id)
        if self.schema_version != 1 or type(self.attempt) is not int or self.attempt < 0:
            raise ValueError("unsupported payload version or attempt")
        if not isinstance(self.url, str) or not isinstance(self.domain, str):
            raise ValueError("URL and domain must be strings")
        if len(self.url) > 8192 or len(self.domain) > 255:
            raise ValueError("oversized URL/domain")
        for digest in (self.content_hash, self.screenshot_hash):
            if digest is not None and not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("object reference must be a SHA-256 hex digest")
        if type(self.http_status) is not int or not 0 <= self.http_status <= 599:
            raise ValueError("invalid HTTP status")
        if type(self.tier) is not int or self.tier not in (1, 2):
            raise ValueError("invalid fetch tier")
        if type(self.not_modified) is not bool or type(self.blocked_by_robots) is not bool:
            raise ValueError("invalid outcome flags")
        if type(self.needs_render) is not bool:
            raise ValueError("invalid render flag")
        if self.fetched_at is not None and not isinstance(self.fetched_at, str):
            raise ValueError("invalid fetch time")
        if self.error is not None and (not isinstance(self.error, str) or len(self.error) > 1000):
            raise ValueError("invalid error category")

    def encode(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def decode(cls, raw: bytes) -> "JobPayload":
        if len(raw) > 32_768:
            raise ValueError("oversized job payload")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("job must be an object")
        return cls(**value)

    def with_attempt(self, attempt: int) -> "JobPayload":
        return replace(self, attempt=attempt)


@dataclass(frozen=True)
class ChangeEventPayload:
    event_id: str
    monitor_id: str
    user_id: str
    detected_at: str
    kind: str
    score: float | None
    cursor: str = ""

    def encode(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode()

    @classmethod
    def decode(cls, raw: bytes) -> "ChangeEventPayload":
        return cls(**json.loads(raw))

    def as_dict(self) -> dict[str, str | float | None]:
        return cast(dict[str, str | float | None], asdict(self))
