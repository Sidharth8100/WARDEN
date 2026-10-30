# Parts 2 and 3: remaining human-owned prerequisites

This is a prerequisite record, not a request to paste credentials into chat.
The complete implementation/proof status is in `reports/parts-2-3.md` and
`PART23_STATUS.md`. Source changes have not all been deployed to the existing
application containers. Never infer live readiness from a green unit-test gate.

## Disk capacity

On 28 October the 77 GB root filesystem filled during builds/image pulls and
new observability initialization, eventually stopping the existing PostgreSQL
container. Application writers were paused with notice; the database and original
application containers were restored after narrowly scoped session cleanup.
Cleaned only completed fixture scratch, the task uv cache, four exact failed-build
cache IDs and the newly downloaded Grafana image/stopped container. All named
volumes, including Grafana data, are retained. No broad pruning, pre-existing
image deletion or daemon/network changes occurred. Prometheus/Alertmanager/Jaeger
remain stopped; Grafana must be pulled/recreated later. Several GB of usable
headroom is still needed before full builds or starting the new stack.

The human must decide what unrelated/old data may be removed or move Docker's
storage using their own host maintenance procedure. Do not run blanket pruning,
volume deletion, or a daemon/network reconfiguration as an automatic response.
After space is available, use the ordinary Dockerfiles and locked dependencies:

```sh
docker compose build api api2 migrate scheduler fetcher processor alerter
```

Inform the human before restarting app containers. Run the migration first,
then restart the app services; preserve all named volumes. Do not execute
`make migrate-test` against the development database: that target downgrades the
whole schema and is appropriate only for a disposable database.

## One dependency awaiting explicit authorization

Django's PostgreSQL backend needs psycopg (or psycopg2). Neither driver was named
in the part prompt, so standing rule 5 prevents silently installing it. Django and
DRF themselves are in the workspace. Once the human authorizes the driver, run:

```sh
uv add --package warden-analytics 'psycopg[binary]'
```

Do not invent a custom Django backend around asyncpg to bypass this rule.
Django models/admin/DRF views are still pending; the scaffold is not a running
analytics service. Generate `inspectdb` output from the actual schema only after
the driver and read-only role exist, review it, and keep Alembic the sole owner of
application tables. No fake `inspectdb` output or permissive type-check override
was added. Any typing dependency also requires authorization under rule 5;
strict checking must remain enabled.

## Dedicated database roles and runtime configuration

Use a local trusted operator psql session. The existing PostgreSQL container can
consume its already provisioned credentials internally without printing them:

```sh
docker compose exec postgres sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Create distinct login roles `warden_replication`, `warden_analytics`, and
`warden_app` if they do not already exist. Grant REPLICATION only to the first;
SELECT on the required application tables to analytics; ordinary application
DML/sequences to app, without superuser/CREATEROLE/CREATEDB/REPLICATION. Use psql's
`\password role_name` to enter human-selected passwords privately. Preserve
existing roles and grants. For example, after confirming the roles are absent:

```sql
CREATE ROLE warden_replication LOGIN REPLICATION;
CREATE ROLE warden_analytics LOGIN;
CREATE ROLE warden_app LOGIN;
\password warden_replication
\password warden_analytics
\password warden_app
GRANT USAGE ON SCHEMA public TO warden_analytics, warden_app;
-- Grant after the new migrations exist; analytics does not need auth secrets.
GRANT SELECT (id, user_id, domain, state) ON monitors TO warden_analytics;
GRANT SELECT (id, monitor_id, detected_at, kind, score, meaningful, classifier_version)
  ON change_events TO warden_analytics;
GRANT SELECT (event_id, user_id, label, created_at)
  ON feedback_labels TO warden_analytics;
GRANT SELECT (event_id, model_id, probability, shadow_prediction,
              production_prediction, created_at)
  ON shadow_scores TO warden_analytics;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO warden_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO warden_app;
```

Review object-owner privileges for future migrations; do not grant analytics
auth/session/credential tables by using blanket SELECT defaults. Add only further
column/table grants justified by the reviewed analytics models. Reinspect routes
before changing PostgreSQL replication authentication; the last checked project
subnet was `10.200.1.0/24`, disjoint from Ethernet `10.23.32.0/21`, Wi-Fi
`172.31.0.0/18`, Docker bridge `10.200.0.0/24`, and forbidden `172.17.0.0/16`.
A narrowly scoped `host replication warden_replication 10.200.1.0/24
scram-sha-256` pg_hba entry permits that role on this project network. Reload
PostgreSQL configuration, not Docker. Never modify host routes/firewall/network
manager to make replication work. Do not copy that CIDR onto another host without
inspection.

The human creates these ignored owner-only files, with their chosen values:

- `.runtime/part3-replica.env`: `PGHOST=postgres`, `PGPORT=5432`,
  `PGUSER=warden_replication`, `PGPASSWORD=<human-selected replication password>`.
- `.runtime/part3-pooler.env`: `DB_NAME=<existing application database name>`,
  `DB_USER=warden_app`, `DB_PASSWORD=<human-selected app password>`.
- Future analytics runtime: dedicated analytics credentials, replica hostname,
  and a human-owned Django secret; do not reuse the API signing key or DB superuser.

Do not print, commit, or have an agent inspect these files. The opt-in Compose
file consumes them; it does not generate deployment credentials:

```sh
docker compose -f docker-compose.yml -f docker-compose.override.yml \
  -f docker-compose.part3.yml --profile part3-database up -d postgres-replica pgbouncer
```

Verify `pg_is_in_recovery()`, write rejection and replay of a fresh primary
commit. Compare WAL locations and measure replay lag; do not mistake an idle
replica's old last-replay timestamp for proof it is behind. No automatic replica
promotion or destructive reseeding is implemented. Switch ordinary API/processor
connections to PgBouncer only after a real transaction-pooling integration proof;
use `make_engine(..., transaction_pool=True)`. Migrations and LISTEN connections
must stay on the direct primary. The pooler profile alone does not reroute apps.

## Public signed webhook proof

The verifier/crash proof uses an isolated obvious fixture key and an injected
pinned transport, explicitly labeled in its JSON evidence. A real internet proof
requires a human-owned HTTPS receiver with a public destination accepted by
`warden_net` and the same privately provisioned signing key on both sides.
Use a named environment reference `WARDEN_WEBHOOK_KEY_<NAME>` containing at least
32 bytes, and a key ID for rotation. Never use the monitor test allowlist for
webhooks, expose a laptop receiver by weakening SSRF, or generate a deployment
key silently. Add a human-owned alerter env_file only when that material exists.
Mailpit needs no signing key and already has a real SMTP proof.

## Observability addresses

- Grafana (currently stopped), read-only anonymous local development UI: `http://127.0.0.1:13000`.
- Prometheus (currently stopped): `http://127.0.0.1:19090`.
- Alertmanager (currently stopped): `http://127.0.0.1:19093`.
- Jaeger (currently stopped): `http://127.0.0.1:16686`.

Grafana admin creation/login/signup are disabled. No default or generated admin
secret was provisioned. Alertmanager has a local inspection receiver, with no
outbound email/Slack/paging configured. Jaeger uses transient memory storage.
Prometheus worker targets will show unavailable while old uninstrumented app
images remain running; that is expected and must not be reported as a successful
crawl error-budget chaos proof. Rebuild/redeploy, then capture one real trace
across API → scheduler → fetcher → processor → webhook and demonstrate the crawl
SLO alert with controlled queued work and worker interruption/recovery.
