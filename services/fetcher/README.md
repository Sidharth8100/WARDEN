# Fetcher

Untrusted service: HTTP/Selenium, Redis and read/write object storage. It receives
no PostgreSQL URL and its image excludes `warden_db`, SQLAlchemy and asyncpg. A
startup assertion rejects even an empty database URL environment variable.

The worker heartbeats membership independently of slow requests. It consumes its
shard and ingress, adopts dead members' work, renews live pending claims, and
reclaims expired claims with XAUTOCLAIM. A target failure schedules a capped delayed
retry; a per-domain circuit breaker pauses calls during repeated failure. After
eight attempts an error result makes the durable job terminally failed, observable
rather than silently lost.

Raw HTML/screenshots are immutable SHA-256-addressed objects. Result messages carry
references, not page bodies. The input claim is acknowledged only after a result
or retry has been written. A crash between those steps may duplicate work; the
processor's PostgreSQL receipt prevents duplicate durable effects.

The Compose simulator allowlist is explicitly development-only. Current URL
checks do not pin sockets to validated DNS addresses, and Selenium subrequests
need a future egress control; see the report before exposing hostile production
traffic. No network/daemon changes are part of this service.
