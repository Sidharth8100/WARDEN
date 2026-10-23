"""Credential-free ACL policy shared by setup documentation and isolated tests.

Each selector couples its commands to its key/channel permissions. A flat union
would accidentally allow SET on a stream just because rate limiting needs SET.
SCAN is intentionally keyless/advisory and cannot provide key-name privacy.
"""

ACL_SELECTORS: dict[str, list[str]] = {
    "api": [
        "(+get +set +exists ~denylist:*)",
        "(+evalsha +eval +script|load +get +set +time ~warden:rate:*)",
        "(+xadd +xgroup|create ~warden:jobs:ingress)",
        "(+xrange +xinfo|stream ~warden:events)",
        "(+subscribe +unsubscribe +ping &warden:events:live)",
    ],
    "scheduler": [
        "(+get ~warden:metrics:*)",
        "(+xinfo|groups ~warden:jobs:*)",
        "(+evalsha +eval +script|load +exists +get +set +incr +pexpire +del "
        "~warden:coord:lease:scheduler ~warden:coord:fence:scheduler)",
        "(+scan +exists ~warden:coord:members:fetchers:*)",
        "(+xadd +xgroup|create ~warden:jobs:*)",
    ],
    "fetcher": [
        "(+incrby ~warden:metrics:reclaimed)",
        "(+set +exists +scan ~warden:coord:members:fetchers:*)",
        "(+xadd +xgroup|create +xreadgroup +xack +xautoclaim +xpending +xclaim "
        "+evalsha +eval +script|load ~warden:jobs:*)",
        "(+xadd +xgroup|create ~warden:results)",
        "(+evalsha +eval +script|load +time +zadd +zrangebyscore +zrem +xadd "
        "+xgroup|create +incrby ~warden:retry:fetch:* ~warden:jobs:* ~warden:metrics:retries)",
        "(+xadd ~warden:dead:fetch)",
        "(+evalsha +eval +script|load +get +set +exists +del +incr +zrange +zrem "
        "+zadd +zcard +zpopmin +expire +ttl +pttl +pexpire ~warden:cache:robots:*)",
    ],
    "processor": [
        "(+incrby ~warden:metrics:reclaimed)",
        "(+xgroup|create +xreadgroup +xack +xautoclaim +xpending +xclaim "
        "+evalsha +eval +script|load ~warden:results)",
        "(+xadd ~warden:dead:processor)",
        "(+evalsha +eval +script|load +get +set +xadd ~warden:events ~warden:eventids:*)",
        "(+publish &warden:events:live)",
    ],
    "health": [],
}


def acl_line(identity: str, password_hash: str) -> str:
    """Render policy with an externally supplied SHA-256 hash, never generate it."""
    if len(password_hash) != 64 or any(c not in "0123456789abcdef" for c in password_hash):
        raise ValueError("expected external SHA-256 hex password digest")
    selectors = " ".join(ACL_SELECTORS[identity])
    return (
        f"user {identity} reset on #{password_hash} -@all +ping +hello +client|setname {selectors}"
    )
