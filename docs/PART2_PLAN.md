# Part 2 — Phase A findings and implementation plan

28 October 2026. **Design/reconnaissance only. Phase B waits for `go`.**
The four explicit Part 2 checkpoints then wait for `continue`.

## Verified starting point

- Part 1 implementation head is `04ad33a`. The prior documentation revision and
  the pre-existing SSRF work remain uncommitted. No clean-working-tree claim.
- No `docs/dsl.md`, rule package, parser, evaluator, rules router, alert relay,
  receiver or delivery-log implementation existed at Phase A start.
- `Monitor.rule_src` is stored/returned by CRUD without compilation today.
- `ChangeEvent.extracted` is nullable and the processor does not populate typed
  prices. Existing classifier features are not a product-price extraction schema.
- The processor locks the monitor, commits snapshot/ChangeEvent/receipt/event
  outbox together, and has an idempotent receipt fast path. Preserve it.
- Existing `Outbox` has unique idempotency key, available time, attempts and
  pending/sent/failed status, but no delivery lease, endpoint snapshot or attempt
  ledger. Reuse it with migrations; do not build a second competing outbox.
- Processor event relay already uses `FOR UPDATE SKIP LOCKED`; no alert HMAC,
  LISTEN/NOTIFY delivery relay, jittered alert retry or delivery DLQ exists.
- `warden_net.validate_url_is_safe` validates URLs; `safe_get` implements GET,
  not signed POST. Validation and HTTP connection currently resolve separately.
  A new webhook adapter must close that DNS pinning gap rather than copying it.
- Fetcher has no DB capability; compare has no network dependency. Rule evaluation
  belongs in the trusted processor, with a pure rule library and injected history.
- `mailpit` already exists in Compose. Root dependencies include Hypothesis,
  asyncpg via DB, HTTPX and Redis via existing packages. No parser library is
  necessary. The only new external dependency planned is `google-re2`, explicitly
  authorized by the human, added with `uv add` in Phase B. None added in Phase A.

## Current evidence and runtime blocker

Initial checks: `make check-todos` **66 passed, 4 skipped, 241 deselected**;
private PostgreSQL/Redis integration suite **15 passed**. After the recovery fix,
default `make check` passed **258/21 skipped/40 deselected**, and the expanded
private suite passed **17**. This distinguishes the initial reconnaissance from
the final source state.

Host routing: Ethernet `10.23.32.0/21` via gateway `10.23.32.1`; Wi-Fi
`172.31.0.0/18` via `172.31.0.1`; Docker bridge `10.200.0.0/24`.
Daemon JSON contains only `bip=10.200.0.1/24`; `docker info` reports no configured
default address pools. `warden_default` still records forbidden `172.17.0.0/16`,
with its bridge absent. All WARDEN containers were observed exited.

The new human policy supersedes the blanket subnet ban. A reviewed local override
selects `10.200.1.0/24`, disjoint from all current connected routes and existing
bridge. This does not certify unknown future VPN/campus destinations. It must be
rechecked before creation. No daemon, host route, firewall or network-manager
setting was edited. Automatic allocation is not safe on this daemon today.

Automatic approval review initially rejected `docker compose down` because the
old hold revoked its prior one-time authorization. The human then explicitly
approved scoped recovery: only WARDEN containers/stale network **without `-v`**,
retain all volumes/credentials, rebuild/start and run the Part 1 proof. No removal
workaround preceded approval. Network recreation and builds completed, and host
routes were preserved. The first proof timed out on processor starvation from
older missing-object results; a tested narrow terminal classification was added,
the stalled proof monitor's 503 receipts drained, and a fresh proof passed:
500/500, no lost/duplicate durable effects, 12 kills, three shared-counter reclaims,
cross-replica 1.287 seconds. The artifact is `reports/part-1-gate2.json`.
Final checks: 258 default tests and 17 private integration tests passed. Containers
remain running; host routes/localhost bindings were verified and Ethernet gateway
ping succeeded. No Part 2 implementation gate is implied by this Part 1 result.

The approved recovery rechecked project ownership/routes, replaced only the stale
runtime without volumes, inspected the new subnet and preserved Ethernet/Wi-Fi
routes. Images rebuilt and actual dependency/proof traffic succeeded. Old queued
obligations drained before the final fresh 500-ID proof. Receipt/snapshot/event/
outbox audits, cross-replica delivery, kills and reclaims are recorded in the
artifact. The earlier interrupted/timeout attempts remain failed evidence.

## Architecture and transaction boundaries

```text
API: compile/validate source and owner-scoped endpoint references
                   |
Processor: classify -> build typed context -> evaluate under monitor lock
                   |
one PostgreSQL transaction:
  ChangeEvent + rule evaluation record + cooldown reservation + tag effects
  + existing realtime outbox + alert outbox intents
                   |
after commit: PostgreSQL NOTIFY wakes alert relay (periodic scan repairs misses)
                   |
relay claims due alert outbox using SKIP LOCKED, records expiring attempt lease
                   |
commit claim -> safe signed network I/O outside DB transaction
                   |
conditional attempt completion OR rescheduled retry OR failed/DLQ
```

`libs/warden_rules` will contain tokens, spans, AST, lexer, Pratt parser,
pretty-printer, checker, RE2 adapter and bounded evaluator. Its only external
runtime dependency is RE2. No database, Redis, HTTP, filesystem or secret lookup
inside the language; injected async history offers a small typed interface.
Add a separate `services/alerter` with trusted DB access and outbound HTTP/SMTP.
This is a warranted service boundary because delivery retries must not block
classification. It never lends its credentials to fetcher or compare.

## CP1 — language core

Implement [dsl.md](dsl.md) as the v1 contract, not regex substitution/eval.
The lexer must advance on each iteration, retain exact spans and bound source,
tokens and literals. Pratt prefix handlers parse literals, grouping and unary
operators; infix handlers use explicit binding powers. Reject trailing tokens,
chained comparisons, unbounded depth and malformed action lists.

The printer is a serializer for AST structure, not original whitespace. Normalize
span-free ASTs for `parse(print(parse(source)))` equality and print idempotence.
Use independent operator trees/evaluation examples to catch a mutually wrong
parser/printer. At least 120 conformance cases across valid/invalid categories.
Seeded AST generation and token mutation produce at least 50,000 programs under
a child-process watchdog; report valid/invalid/limit-rejected counts separately.
Unexpected exceptions fail, rather than counting swallowed errors as successes.
Finish `make check`, record JSON evidence and stop for `continue`.

## CP2 — validation, context, history and evaluator

Add a typed field registry, Optional semantics and compile-time signatures.
Bounded Levenshtein suggests visible declared names only. Every error carries
exact source span; this is the selected design-authority addition. A future UI
can underline a misspelling rather than display a generic failure. Starter
templates and a historical dry-run are useful, but precise diagnostics and typed
replay context take priority over expanding the API in this part.

Implement pure typed price extraction with explicit selector, currency and
decimal conventions; persist versioned context in `ChangeEvent.extracted`.
No guessing prices from arbitrary page digits. Add immutable RuleRevision,
RuleEvaluation and RuleState records. One active rule per monitor initially;
preserve `Monitor.rule_src` as a compatibility mirror. Existing source must be
validated before activation. If application paths update source, they must use
the same compiler and optimistic-concurrency/owner checks.

Interpreter steps and a monotonic deadline protect CPU work; async history calls
also have timeouts. RE2 compilation/matching is bounded; unsupported syntax fails
without fallback. Use Decimal, reject zero division/overflow and cap values.
No generic `getattr`, Python `eval` or dynamic function name dispatch.

Redis window sets contain only committed prior event IDs and timestamps. Compare
the projection watermark with PostgreSQL under the existing monitor lock; repair
from committed history before trusting counts. Exclude the uncommitted current
event, never count rolled-back attempts, and retry safely on Redis failure.
An event-ID set avoids retry inflation. Reserve cooldown and tags in PostgreSQL
with the event/actions so a rollback cannot suppress a later real alert.

Extend source-controlled credential-free ACL policy plus private fixtures for
the new minimum key scopes. **Do not read/edit deployed ACL or runtime secrets.**
At the point deployment needs extra role credentials/ACL changes, stop and give
the human exact scoped provisioning commands; no new production secrets are
needed just to implement/fuzz/test the library.

Tests cover types, Missing/not, suggestions, short circuits, steps/deadlines,
RE2 rejection, exact window/cooldown boundaries, Redis loss/rebuild and concurrent
processing/rollback. Finish check and stop for `continue`.

## CP3 — delivery, network safety and recoverability

Reuse Outbox with isolated `alert.*` kinds so existing fetch/event relays do not
consume them. Add lease owner/token/expiry fields, immutable destination version,
bounded error metadata, Delivery and DeliveryAttempt records and indexes. A DLQ
is the durable failed delivery state with queryable final reason, not lost rows.

In a short transaction, claim due rows with `FOR UPDATE SKIP LOCKED`, increment
attempt token, record lease and commit. Perform network I/O outside locks. Finish
only if the attempt token still matches. After a relay death the lease expires
and another relay resumes; stale attempts cannot overwrite newer success.
Bounded request deadlines must be shorter than leases, with renewal if required.
Retain the stable logical delivery ID on retries.

Use dedicated asyncpg LISTEN connection, commit LISTEN before initial scan,
then scan on wakeup plus periodic timeout. Emit `pg_notify` in the event/outbox
transaction; notifications are hints, not durable jobs. Reconnect and rescan.
Do not place credentials, bodies or endpoint URLs in notification payloads.

HMAC wire contract:

```text
Webhook-Id: <stable logical delivery UUID>
Webhook-Timestamp: <attempt timestamp, integer UTC seconds>
Webhook-Key-Id: <registered non-secret version identifier>
Webhook-Signature: v1=<hex HMAC-SHA256>

signed bytes = ASCII(timestamp) + b"." + ASCII(delivery_id) + b"." + raw_body
```

Persist canonical UTF-8 body bytes. Re-sign each retry with a fresh timestamp;
retain logical ID and content. Receiver rejects timestamps outside ±300 seconds,
malformed/oversized bodies and unknown key IDs, verifies with `hmac.compare_digest`,
then atomically inserts its dedup receipt and local effect in one transaction.
Concurrent/repeated valid deliveries return success without repeating the effect.
An identical signature does not itself provide replay prevention or encryption.
Use HTTPS in production; never log secret/signature/secret-bearing URL values.

Timestamp freshness alone permits replay inside the window; the stable-ID receipt
is required. A sender crash after receiver commit but before recording success
necessarily permits another HTTP attempt. Promise at-least-once requests plus
idempotent receiver effect, not exactly-once network delivery. A third-party
receiver must provide its own idempotency support; WARDEN cannot enforce it.

Webhook registration and every attempt must pass existing `warden_net` safety
checks with **no allowlist exception**. Reject URL userinfo/fragments, disallow
redirects for webhooks, validate all DNS answers, then connect only to a validated
IP while preserving HTTP Host and TLS SNI/certificate checks. An extension of
`warden_net` will own this pinned POST transport; do not merely validate then let
a second DNS lookup choose a different IP. Use existing dependencies/stdlib
transport primitives; request approval before any extra connector dependency.
Ignore proxy environment variables, bound body/response/connection/read/total
time, do not send cookies or credentials to redirect destinations. Revalidate
edits, retries and manual redelivery. SSRF denial is a terminal policy failure.

Retries: capped exponential full jitter, bounded Retry-After support, retry
timeouts/connection errors/408/429/5xx, terminal ordinary 4xx and redirects, bounded
attempt count and delivery age. Per-endpoint persisted breaker: closed/open/
single leased half-open probe shared across alerter replicas. A breaker deferral
does not count as an HTTP attempt. Metrics distinguish blocked, deferred,
retried, delivered and dead. Avoid one failing destination starving others.

Reference receiver has a persistent dedup/effect transaction, body-bound tests,
current/previous key versions and explicit retention. Keys are human-owned for
deployment; fixtures use non-deployment test material in private temporary state.
No endpoint secret may be generated/stored silently by the agent.

Proof must kill the relay **after receiver effect commit and before sender success
commit**, then restart and demonstrate another attempt with the same logical ID
and exactly one receiver effect. Random kills alone do not prove that boundary.
Include both relays racing, expired leases, receiver restart, fresh retry signature,
timeout ambiguity and DLQ transition. Use real private PostgreSQL/Redis and an
injected receiver transport for deterministic integration. This proves the
application transaction path, not real DNS/TLS interoperability.

A fully live network proof with the strict SSRF policy needs a human-controlled
public HTTPS reference receiver. A private Compose/localhost URL must be rejected,
even for this proof. Do not add a webhook allowlist to make the test green; record
the remaining wire-proof prerequisite and request that public endpoint/secret
provisioning only when needed. Published local ports remain 127.0.0.1.
Finish check and stop for `continue`.

## CP4 — email and delivery operations

Use standard-library SMTP email construction and an async wrapper with actual
socket/overall deadlines; use existing Mailpit as a fixed operator destination,
not a user-supplied arbitrary SMTP URL. Reject CR/LF header injection and bound
body/recipient sizes. Only owner-registered authorized recipients are usable.
Delivery acceptance by SMTP is not proof of inbox placement or human reading;
ambiguous SMTP acknowledgments can still duplicate mail. Mailpit proves local
capture/content; production reputation/bounces are future work.

Delivery log APIs scope every query/mutation to authenticated owner, paginate by
stable cursor, show per-attempt outcome and redact destinations/error details.
Manual redelivery uses existing request idempotency support and an audited
transition, revalidates current permission/destination, creates a new request
record while preserving the same logical delivery ID/body for recovery of the
same effect. It does not rerun the rule or consume cooldown again. Deliberately
sending a second business notification would need a separately named action,
not an accidental repeated button click. Failed/sent rows remain auditable.

Test Mailpit capture, header handling, transient failure, owner isolation,
pagination, repeated redeliver request, concurrent relays and audit trail.
Finish check, report all evidence and stop at CP4 before calling the part complete.

## Phase C and completion criteria

Expand `docs/reports/part-2.md` in the exact nine-section Report Spec order.
Replace proposed snippets with 20–40-line actual source tours; include 25+
three-to-six-sentence interview answers, five honest gotchas, three-to-five
isolated labs and the actual authorship diff. Record dependency changes via
`uv add`, test seeds/case counts, proof timing and unresolved prerequisites.
Do not imply Phase A documentation has passed any CP gate or delivered alerts.
