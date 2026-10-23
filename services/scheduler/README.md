# Scheduler

Trusted service: PostgreSQL plus the named `scheduler` Redis identity. Start with
`docker compose up -d scheduler` after human credential provisioning and migrations.

A Redis lease selects a leader; its monotonically increasing token must also pass
`warden_db.dispatch.fence` inside every protected PostgreSQL transaction. The
shared fence row serializes takeover with the preceding term's database writes.
A lease alone does not prevent a paused process from resuming.

On boot and every five seconds, active monitors are reconciled into a hierarchical
100 ms timing wheel. Ticks use the monotonic clock; persisted deadlines use UTC.
Every minute, recent completed checks feed interval-censored Poisson MAP estimates
and a bounded Lagrangian allocation. Each monitor's plan floor and maximum interval
is applied inside the solver. An infeasible budget fails visibly instead of
silently overspending. `WARDEN_CRAWL_BUDGET_PER_SECOND` defaults to 1.

Scheduling commits a stable job ID, receipt and dispatch outbox before Redis
publication. The relay writes to a membership-derived fetcher shard (or ingress),
then marks the outbox sent. Repeating publication is safe for durable effects.
Consumer-group pending plus lag supplies an approximate bounded-load ring input;
this is routing guidance, not globally exact capacity enforcement.

Review `docs/reports/part-1.md` for measured results, limitations and the handoff.
Never reset the Redis fencing counter to recover a failed term.
