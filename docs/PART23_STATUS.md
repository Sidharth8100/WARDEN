# Parts 2 and 3 execution record — 28 October 2026

Authorization: complete both parts without checkpoint pauses or approval questions.
The combined report is `reports/parts-2-3.md`. **Both parts are not fully complete.**
Do not mistake an old healthy container for deployment of the current source.

## Implemented / measured

- Handwritten bounded lexer/Pratt parser/printer/checker/interpreter; exact source
  spans and Levenshtein suggestions; RE2. Language suite: 199 tests.
- Seeded fuzz: 60,000 programs, 45,745 accepted, 14,255 rejected, 45,000 deliberately
  valid, zero unexpected exceptions/hangs; normalized AST round-trip and printer
  idempotence. JSON: `reports/part-2-fuzz.json`.
- Immutable active rule revisions; explicit price extraction/context; durable
  evaluation/cooldown/tag/alert intent inside the ChangeEvent transaction.
  Redis is a repairable committed-history count projection using existing GET/SET
  ACLs, with cache identity tied to current event/time and prior-event watermark.
- Leased/fenced SKIP LOCKED outbox relay, dedicated LISTEN session, fresh signed
  retries, pinned HTTPS SSRF transport, per-endpoint breaker, persisted dead letters,
  verifying SQLite receiver. Real private PostgreSQL SIGKILL proof: two attempts,
  one committed receiver effect. Network transport is injected and labeled.
- Rule diagnostics/dry-run; owner-scoped destinations, deliveries/attempts and
  idempotent manual redelivery; immutable feedback/model APIs.
- Real SMTP Mailpit proof and delivered DB ledger. A repeat revealed that Mailpit
  `total` is global mailbox size; assertion corrected to check `messages_count`
  AND returned matching messages. No messages deleted or external email sent.
- Handwritten online logistic SGD, signed hashing/L2/prior, per-account immutable
  weights/shadow scores, server-held-out F1 promotion gate, alias rollback CLI.
- Synthetic price-drop-preference curve: static F1 0.671480, final F1 1.000,
  1,200 training/600 independent held-out samples, 93 held-out positives. This is
  synthetic personalization evidence, not real-site generic classifier quality.
- Real PostgreSQL fixture query plans, five repetitions each; latest median ms:
  queued count/age 6.184→0.026; SLO 5m population 9.309→0.058;
  owned holdout 5.929→0.311. Exact latest plans are in the JSON/report.
  Warm cache, 150,002 crawl/30,000 learning rows; three chosen app hot paths,
  not an exhaustive production slow-query ranking.
- Real PostgreSQL 16 streaming standby replay and write rejection, private Unix
  sockets. Optional deployment replica/pooler config prepared, not started.
- Queue trace context, SDK propagation/redaction tests, JSON log correlation,
  durable SLO sampler with stale-data protection; Prometheus/Grafana/Jaeger/
  Alertmanager config. promtool config and three alert-rule tests pass; amtool
  config passes. Grafana anonymous Viewer, no admin secret created.

## Verification and resource incident

- Final completed make check: 490 passed, 21 skipped, 53 deselected; strict mypy
  passes all 95 source files. No rules/types/tests weakened.
- Full new private integration suite: 13 passed, including >10,000-event bounded
  count projection, SIGKILL retry/deduplication, ownership/redelivery, real SMTP,
  held-out gates, real EXPLAIN plans and real streaming standby.
- Existing private recovery/Redis suite: 17 passed on the final rerun. Earlier
  disk-full attempts (including six failed initdb setups and the Mailpit counter
  mistake) are retained in the report, followed by successful corrected reruns.
- Final fuzz: 60,000 cases, zero unexpected exceptions/hangs, 17.915 seconds.
- Prometheus config and three promtool rule tests passed; amtool config passed.

The 77 GB root filesystem filled repeatedly and PostgreSQL exited. After notice,
application writers/new telemetry were stopped. Cleanup was limited to completed
session scratch, the dedicated uv cache, exact failed-build IDs
`t686o0pe2d8yxdunjfanvf01l`, `kyvaxlp15ptr6hh9g356g2a73`,
`yzrn7ba8a0ui1thb3uysrqoll`, `o5fc22tr6whideypb42ds4284`, and the newly downloaded
Grafana image/stopped container. All volumes and older images/cache are retained; the newly downloaded Grafana
image and its stopped container were removed after notice, preserving its volume.
No broad pruning, credential-file access or network/daemon changes. PostgreSQL
and original app containers were restored; both APIs /ready returned 200.
New telemetry is stopped and Grafana image/container removed; its volume/config
remain. The live schema is still e021a1000001; source migrations reach
e023a1000002. Only a new scheduler image finished building, and the existing
running scheduler still uses its old image. Several GB of safe storage are needed.

Routes remained Ethernet 10.23.32.0/21, Wi-Fi 172.31.0.0/18, Docker bridge
10.200.0.0/24 and project 10.200.1.0/24, with no forbidden 172.17 route.

## Remaining required work

1. Human frees several GB safely; no automatic broad Docker cleanup. Finish
   ordinary image builds and migrations/deployment, with notice before disruption.
2. Human authorizes psycopg under dependency rule 5 and provisions dedicated DB
   roles/Django secret. Exact commands and file fields: `PART23_SETUP.md`.
3. Implement/verify actual inspectdb-reviewed unmanaged Django models, read-only
   replica routing, authenticated DRF analytics and admin. No fake inspectdb,
   custom asyncpg Django backend or permissive typing overrides.
4. Deploy/verify replica/PgBouncer and actual transaction-pooling compatibility;
   route ordinary DB traffic through pooler. Keep migrations and LISTEN direct.
5. Capture a real Jaeger export across all services; perform queued-work/worker-kill
   crawl SLO demonstration with guaranteed worker recovery.
6. Human-owned public HTTPS receiver/signing material for actual DNS/TLS webhook
   proof; no SSRF/allowlist exceptions. Repeat final full/integration gates.

Pre-existing unverified core `ssrf.py` diff stays untouched (153 added/47 removed),
uncommitted. Preserve unrelated dirty files. No commits/pushes. Never open actual
credential files to resume.

## 30 October development-history checkpoint

The human authorized twelve focused commits and publication to `fast-api-dev`.
The current checkpoint supersedes the earlier instruction to leave the pending
source and pre-existing SSRF diff uncommitted. SSRF contents are unchanged and
its work date (26 October) was supplied by the human; original authorship stays
unverified. Implementation author dates use the documented 28 October milestone;
documentation is dated 30 October. Actual commit-creation timestamps stay current.
Main remains unchanged because the required-work list above is still open.
See the final Handoff in `reports/parts-2-3.md` and inspect local/remote branch
HEADs for actual publication state. No deployment or infrastructure change is
part of this version-control operation.
