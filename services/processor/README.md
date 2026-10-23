# Processor

Trusted service: PostgreSQL, named `processor` Redis identity, read-only object
storage. The `warden_compare` library remains a pure computation dependency; the
service supplies its input bytes and stores its results.

For each result, lock the monitor and durable job receipt. A queued receipt is
completed together with its snapshot, optional meaningful change and event outbox
in one PostgreSQL transaction. A completed receipt makes redelivery a no-op. Hash
verification rejects a mismatched object. A Bloom hit is only a hint and requires
an exact previous-content check before skipping comparison.

After commit, publish the event outbox to the retained Redis Stream, then Pub/Sub,
then mark it sent. Retrying after a crash deduplicates the stream append for seven
days; the gateway uses stream cursors and a bounded recent-event cache. Redis
retention is finite, so old clients must resync instead of assuming infinite replay.

Object writes are intentionally unavailable. Runtime snapshots reference raw
immutable keyframes; the independently tested delta codec is not persisted by this
service. Monitor processing is serialized in result-arrival order, not guaranteed
fetch-time order. Full operational and learning details: `docs/reports/part-1.md`.
