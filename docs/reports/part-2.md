> Current implementation, evidence and Handoff: [combined Parts 2/3 report](parts-2-3.md).
> This file retains the original Phase A analysis as a historical design record.

# Part 2: Rule DSL and alerting — Phase A report

28 October 2026. **Reconnaissance and design, not a completed Part 2.**
Read with [the proposed language contract](../dsl.md) and
[the checkpoint plan](../PART2_PLAN.md). This Phase A edition uses the nine-section
Report Spec structure. Phase C must expand the actual implementation tour and
measured proofs into the full multi-hour learning report. No CP1–CP4 feature,
50,000-program fuzz run or signed alert delivery is claimed by this document.

## 1. Executive summary

WARDEN already accepts checks, retrieves pages, compares versions and publishes
meaningful ChangeEvents. Part 2 will let a person specify which changes matter
to them and what should happen next. A price watcher might want a notification
only when a price drops ten percent, falls below a budget and has not already
triggered a recent alert. The classifier identifies a meaningful change; a rule
expresses one user's policy about that change. Delivery then carries that policy's
result to a webhook or email recipient, despite temporary outages and restarts.

Phase A inspected the checkout rather than treating planning as implementation.
It found a stored rule-source column and reusable transactional outbox, but no
language lexer/parser/checker/interpreter, alert relay or delivery log. It also
found that ChangeEvents do not currently contain populated typed prices. A price
rule cannot honestly be implemented by treating a classifier score as a price or
taking the first number on a page. The design therefore includes explicit price
extraction, currency and decimal conventions, and missing-data semantics before
offering seemingly simple threshold examples.

The new artifacts are a proposed v1 DSL contract, an implementation/proof plan
with four checkpoints, and this source-grounded learning report. The DSL will
have a hand-built lexer and Pratt parser, static type checking, a bounded
tree-walking evaluator and RE2 for user regex. It will have no Python execution,
general loops or arbitrary access to application objects. The selected worthwhile
addition is structured errors with exact source spans and bounded spelling
suggestions, helping both beginner users and a future editor highlight mistakes.
These are design commitments to implement after approval, not shipped features.

Alert intents will reuse the existing outbox inside the ChangeEvent transaction,
alongside cooldown reservations and tag effects. A separate relay will claim
work briefly, release database locks before network I/O, sign exact body bytes,
and record retryable outcomes. A sender crash after the receiver commits makes
another HTTP attempt unavoidable. The reference receiver must deduplicate a
stable delivery ID in the same transaction as its effect; WARDEN cannot promise
exactly-once effects at arbitrary third-party receivers. Email has a similar
acknowledgment ambiguity and Mailpit proves capture rather than inbox placement.

This session also resumed Part 1 recovery under the human's new Docker policy.
Inspection found stale forbidden subnet metadata and no safe automatic address
pool. The local override selects a route-checked project subnet without changing
daemon or host networking configuration. Fresh scoped approval was obtained after
automatic review rejected the initial removal. Independent integration and default
quality checks passed. The final runtime proof result is recorded in the Handoff;
this report never treats an earlier interrupted run as a successful one.

## 2. Concepts from scratch

### 2.1 A language is a deliberately small machine

An order form offers useful choices without permission to redesign the kitchen.
A domain-specific language, or DSL, similarly restricts what a user can express.
WARDEN needs conditions over change data plus a small set of actions, not general
Python. Source text becomes tokens, then a syntax tree, then a checked tree and
finally an evaluation result. Each representation establishes facts the next
stage can trust, and each stage can fail with a different useful diagnostic.

The restriction is a product and security decision. A user should describe a
price threshold without knowing database internals. An attacker should not turn
the same text box into imports, arbitrary functions, file reads or HTTP requests.
Replacing words in a string and passing it to Python eval would retain a much
larger language than we intend. Explicit stages also make correctness teachable:
ask which representation first became wrong rather than debugging everything.

### 2.2 Lexer: cut the sentence into labeled cards

Imagine cutting a sentence into word cards while recording where each came from.
A lexer walks characters and emits tokens: number, field, operator, keyword and
so on. It distinguishes a quote within a string from the quote ending it, and
a unary minus operator from a numeric token. A source span uses half-open offsets,
so a diagnostic highlights the precise characters that caused the problem.

The algorithm is a cursor/state machine. Every successful loop consumes input;
a malformed sequence produces a bounded error instead of retrying forever.
Size limits apply separately to input, token count and individual literals.
WARDEN must reject malformed escapes, oversized decimals and unclosed strings
without IndexError or hangs. Offsets are Unicode code points by contract; a future
browser editor can convert them to UTF-16 offsets when necessary. Location bugs
under emoji are still correctness bugs, not merely presentation issues.

### 2.3 Parser: turn a sentence into an explicit structure

Two plus three times four needs an agreed grouping. A parser converts tokens
into an abstract syntax tree, or AST: add(2, multiply(3,4)) in this example.
Parentheses can change the tree; spacing usually cannot. The AST stores meaning-
relevant structure instead of presentation. A syntax error means we cannot build
a legal tree; it does not yet say whether a legal field name or operation exists.

WARDEN's parser also separates condition expressions from actions after `then`.
The plus between webhook and tag separates actions; plus within a condition is
numeric arithmetic. Full input consumption is mandatory. Accepting a legal prefix
and ignoring trailing junk could execute something different from what a person
thought they saved. The grammar rejects unbounded nesting, arbitrary calls,
assignments and chained comparisons. A small deliberate grammar is easier to
reason about than an accumulation of undocumented convenience cases.

### 2.4 Pratt parsing: operators have binding strength

Think of an operator pulling neighboring expressions into its group. A Pratt
parser first handles the start of an expression: a literal, field, parenthesis
or prefix operator. It then consumes infix operators only if their binding power
meets the current minimum. The recursive call for the right-hand operand uses a
new minimum, which determines both precedence and associativity.

Multiplication binds more tightly than addition, so it is admitted inside the
right operand of addition. For left-associative subtraction, the right call must
not consume another subtraction at the same strength: ten minus three minus two
means (ten minus three) minus two. Parentheses start a fresh grouped parse until
their close token. This is precedence climbing: recursive calls admit stronger
operators, rather than needing a separate grammar function for every level.

The proposed table puts `or` below `and`, comparisons below arithmetic, and prefix
operators highest. Consequently `not` is deliberately tight; write explicit
parentheses around a comparison to negate it. Independent expected AST cases
must prove the table. A parser and printer can share the same wrong precedence,
so agreement between those two alone cannot prove the intended language.

### 2.5 Pretty-printer: serialize structure without changing it

Copying a map more neatly must not change where the roads go. A pretty-printer
turns an AST into canonical source: stable decimal spelling, string escaping and
sufficient parentheses. It may discard comments and redundant spacing, but not
tree structure or literal values. Round-trip equality therefore ignores spans,
which necessarily change when layout changes, while retaining semantic nodes.

Two useful properties are parse(print(tree)) equals normalized tree, and printing
the printed result is idempotent. Independent semantics examples remain essential.
Fuzzing generates many varied trees and malformed token streams, with a fixed seed
and a subprocess watchdog. Expected parse errors count as controlled rejection,
not crashes. A catch-all handler that swallows every exception cannot establish
50,000 crash-free cases; the evidence must distinguish successes, diagnostics,
resource-limit rejections and unexpected failures.

### 2.6 Static typing: inspect the recipe before cooking

A recipe asking whether a temperature is less than the word cheap is nonsensical
before the oven turns on. A static checker verifies names, operand types and
function signatures without running the rule. WARDEN can reject comparing a
Decimal price with a String at save time. Even an unreachable branch is checked,
because otherwise a later edit could expose a latent error.

Runtime guards remain necessary for data-dependent failures such as dividing by
zero. Optional fields can be absent in one event; a misspelled field cannot.
Conflating them would turn a typo into a silently nonfiring rule. The proposed
Missing comparisons return false, including not-equal, but negation can turn that
false into true. Users requiring presence should include `exists(field)`, especially
with `not`. An error, absence, false condition and cooldown suppression are distinct
outcomes that deserve different records rather than one vague no-alert status.

### 2.7 Levenshtein: a small edit-distance table

Correcting a word can involve inserting, deleting or replacing letters. Levenshtein
distance computes the smallest number of those edits using a dynamic-programming
table over two prefixes. Each cell chooses the cheapest preceding insertion,
deletion or substitution. With a small fixed field vocabulary, comparing a typo
against every known field is inexpensive and easy to test independently.

WARDEN's suggestions are bounded by name length, distance and returned count.
They are suggestions, not silent rewrites: choosing a different field changes
policy. Only the visible declared schema is eligible, never another tenant's
endpoint names or private object attributes. Exact source spans locate the typo.
This is the chosen additional design feature because it improves every invalid
edit and gives a future frontend a stable machine-readable interface.

### 2.8 Typed prices: the first number is not the price

A product page may contain a model number, review count, discount and price.
Taking its first number is like paying the street number on a restaurant bill.
Explicit selectors, currency and decimal/grouping conventions establish which
value we mean. The classifier's numeric-change feature does not provide that
contract. An absent or ambiguous extraction must not become a fabricated zero.

Use Decimal for threshold calculations, require a positive previous price for
percentage drop, and reject incompatible currency or excessive magnitude.
Persist the typed context and schema version with the event so later debugging
does not rerun today's extractor against yesterday's implicit assumptions.
These rules do not prove a website is truthful; they make our interpretation
explicit. A zero current value can exist, while a zero previous value makes the
percentage undefined. Those cases belong in separate tests.

### 2.9 Evaluation budgets: charge for work, not only time

A prepaid machine deducts a credit for each operation and stops when credit runs
out. A bounded tree walker charges steps, checks a monotonic deadline and restricts
AST depth, strings and numeric magnitude. Redis calls also have timeouts. Wrapping
an infinite synchronous loop in an asyncio timeout does not interrupt it: the
loop never yields for cancellation to run.

Bounds therefore belong inside every stage, plus an external fuzz watchdog.
One regex node can hide large work even when the AST is small, so pattern and
input limits are separate. The proposed evaluator rejects oversized text contexts
instead of quietly truncating and missing a keyword near the end. CPU-safe
evaluation is a composition of limits, not a claim obtained from the word async.
CP1/CP2 will measure the proposed defaults and record any justified adjustment.

### 2.10 RE2: restrict the regex engine as well as the rule grammar

Some regex engines explore many alternative paths and repeatedly backtrack when
a near-match fails. A user-controlled pattern and untrusted long page can then
consume disproportionate CPU even if the pattern compiled successfully. That
threat matters here because WARDEN repeatedly evaluates user-supplied regex.
Syntax validation alone is insufficient.

RE2 provides a restricted engine with documented safety/complexity goals and
omits constructs such as backreferences and lookaround. Bound pattern length,
memory and text input too; linear in an enormous input still takes work. Unsupported
syntax must return a diagnostic, never fall back to Python `re`. The official
[project](https://github.com/google/re2) and [syntax reference](https://github.com/google/re2/wiki/Syntax)
describe that subset. No binding was installed during Phase A; `google-re2` is
the sole new external dependency planned, explicitly authorized for Phase B.

### 2.11 Windows: history depends on the instant and membership rule

How many changes occurred in a day depends on when we ask and whether the current
change counts itself. The contract counts prior committed meaningful events of
the same monitor, excludes the current event, and uses persisted processing time
with durable IDs breaking ties. This matches Part 1 history ordering; it is not
the website's true publication timeline. Exact boundary inclusion must be tested.

Redis sorted sets order event IDs by time for range counts. But a Redis mutation
does not roll back with a PostgreSQL transaction. Adding the current event before
commit could leave phantom history if rule evaluation or commit fails. The design
therefore caches committed history only, deduplicates event IDs and checks a
watermark against the durable history under the existing monitor lock. Missing
or stale data is rebuilt; Redis failure retries processing instead of guessing
zero. Busy-history rebuilds need indexes and explicit work limits, not truncation
presented as an exact count.

### 2.12 Cooldown: make a durable reservation

A receptionist's promise not to call again for six hours must survive a shift
change. A local dictionary cannot survive service restarts or coordinate replicas.
WARDEN will persist cooldown keyed by monitor/rule revision with the event and
scheduled actions. The same monitor lock orders concurrent evaluations; rollback
undoes the reservation. The exact cooldown end boundary permits the next match.

Cooldown begins when an alert is scheduled, not when delivery succeeds. Otherwise
a failing endpoint could cause every new change to schedule another alert while
the first remains pending. Retry of that logical delivery does not reevaluate
the rule. Editing to a new revision starts new cooldown history in this proposal;
that consequence must be visible in the editing API. Small temporal definitions
shape the product as much as the six-hour number itself.

### 2.13 Outbox: join intent to the event

A shop can record an order and a delivery slip in one ledger entry so both exist
or neither does. The transactional outbox stores the ChangeEvent and alert intent
in one database transaction. That closes the save-then-send gap: a crash cannot
commit the event without its intent, or send an alert for an uncommitted event.
The existing WARDEN outbox is reusable; inventing a second one would divide ownership.

There is still no joint transaction with an arbitrary HTTP receiver. Its response
can be lost after its effect commits. A retry must carry the same logical delivery
ID, and that receiver must atomically deduplicate ID and effect. Local outbox
uniqueness and remote deduplication protect different failure boundaries. Seeing
one successful delivery does not prove either boundary survives a crash.

### 2.14 Locks, leases and the notification doorbell

Workers taking tickets should skip one another's currently claimed ticket rather
than queue behind it. `FOR UPDATE SKIP LOCKED` supports that work-claim pattern;
it does not promise a complete consistent view for ordinary reporting. A short
transaction claims due intents with an expiring attempt token, then network I/O
occurs outside locks. Completion must match that token so stale workers cannot
overwrite a later attempt. See [PostgreSQL's reference](https://www.postgresql.org/docs/16/sql-select.html).

A lease lets another worker reclaim after death but cannot retract a request
already on the wire. LISTEN/NOTIFY is a doorbell: it wakes listeners to inspect
the durable outbox, but does not remember missed letters. Commit LISTEN before
the initial scan, rescan on reconnect and keep a periodic scan. Notify only after
the producing transaction commits, and never put secrets in its payload. The
[LISTEN](https://www.postgresql.org/docs/16/sql-listen.html) and
[NOTIFY](https://www.postgresql.org/docs/16/sql-notify.html) docs describe the startup
race and transaction behavior. Polling remains the repair mechanism.

### 2.15 HMAC: authenticate exact bytes, then prevent replay separately

A shared secret lets a sender stamp bytes so a receiver can detect alteration
and verify knowledge of the key. HMAC-SHA256 is not encryption or a public-key
signature. Persist exact canonical outgoing bytes; a receiver must verify the raw
body before parsing, since reserializing JSON can change bytes without changing
the apparent data. Constant-time digest comparison reduces timing leakage.

The signed input includes timestamp, stable delivery ID and body. A receiver
checks freshness and the digest, then commits its dedup receipt with its effect.
A captured valid request can still replay inside the freshness window; timestamp
validation alone is insufficient. Retry signs a fresh timestamp but keeps ID/body.
Clock tolerance, key versioning and receipt retention are explicit policies.
Python's [HMAC documentation](https://docs.python.org/3.12/library/hmac.html) describes
the comparison primitive; human-owned deployment keys never belong in source,
outbox payloads, reports or chat.

### 2.16 SSRF: a destination field controls our outbound connection

A URL supplied by a user can point our server toward localhost, a private database
or a metadata service. Webhooks therefore need exactly the monitor URL safety
guard, with no simulator-host exception. Validate registration, edits, every retry
and manual redelivery. Reject redirects and userinfo, and bound response size and
connection/total time. A signed request should never follow an unreviewed redirect.

Checking DNS and then resolving again during connection leaves a time-of-check/
time-of-use gap. The POST adapter must connect only to a validated IP while
preserving hostname-based TLS certificate checks and SNI. Ignore environment
proxy routing. An internal test receiver is still a forbidden destination. An
injected transport can test application behavior without proving real DNS/TLS;
a fully live proof needs a human-controlled public HTTPS receiver. We will name
that prerequisite rather than weaken SSRF to make a test green.

### 2.17 Retry, breaker and dead letters

Retry means a temporary failure may succeed later. Backoff increases spacing,
jitter spreads clients rather than synchronizing another surge, and a circuit
breaker pauses a failing endpoint before allowing a controlled probe. Dead-letter
state preserves exhausted obligations for inspection. These are complementary,
not interchangeable names for one retry loop.

Persist per-endpoint breaker/probe ownership across replicas. Do not count a
breaker deferral as an actual HTTP attempt, or retry a policy-denied SSRF target
forever. Bound Retry-After, attempt count and maximum delivery age. Manual
redelivery must be owner-scoped, audited and idempotent too; repeated button clicks
are another duplicate path. Mailpit proves SMTP capture/content, not production
inbox placement, and ambiguous SMTP acknowledgments can still duplicate mail.

## 3. Design decisions and the road not taken

The complete implementation sequence is in [PART2_PLAN.md](../PART2_PLAN.md).
Alternatives below are reasoned proposals, not benchmark results.

| Choice | Alternative considered | Why this choice; cost accepted |
|---|---|---|
| Small hand-built DSL | Embedded Python or parser library | Explicit grammar, prompt requirement and auditable authority; more implementation/testing work |
| Exact spans and suggestions | Start with templates or dry-run | Helps every error and future UI; templates/history replay wait for reliable context |
| Explicit typed extraction | Guess first number or reuse score | Reproducible price semantics; requires user configuration and Missing behavior |
| One active rule, immutable revisions | Many concurrent rules immediately | Bounded initial scope and auditable retries; richer policy composition is deferred |
| Existing Outbox plus isolated kinds | Separate alert queue table | Preserve one transactional foundation; migration/consumer filters require care |
| Dedicated alerter | Send from processor transaction | Isolate endpoint delays/retries; one more deployable service |
| Short claims and leases | Hold row lock through HTTP | Avoid long network transactions; need stale-token/reclaim tests |
| PostgreSQL cooldown, repairable Redis history | Redis-only state | Rollback/durable ownership; projection validation/rebuild costs |
| Reject webhook redirects | Follow after validation | Simpler credential/body destination guarantee; some integrations require canonical URLs |
| Receiver persistent dedup | Promise sender-only exactly once | Honest ambiguous-ack recovery; third-party receivers must cooperate |
| Scoped route-checked local subnet | Change daemon address pools globally | Restore this project without host-wide restart; host-specific range must be rechecked |

Choosing spans does not eliminate the need for a future dry-run. A dry-run should
use immutable saved contexts and an isolated history/cooldown adapter, otherwise
previewing a rule could affect real notifications. Nor does a pure rule library
mean the processor is network-free: it supplies bytes, DB persistence and Redis
history around the library. Preserve the permanent fetcher/compare boundaries.

The new network override is a deliberate exception to the earlier blanket subnet
ban under the human's latest policy. `10.200.1.0/24` is disjoint from the inspected
Ethernet `10.23.32.0/21`, Wi-Fi `172.31.0.0/18`, bridge `10.200.0.0/24` and forbidden
`172.17.0.0/16`. It is not a claim about undiscovered remote VPN/campus ranges.
The daemon still has only a `bip` setting; its automatic pools remain uncorrected.
[Docker's Compose network reference](https://docs.docker.com/reference/compose-file/networks/)
documents the separate project bridge and IPAM configuration. Named volumes and
credentials were retained, and no host route/firewall/daemon configuration edited.

## 4. A guided code tour

These are existing integration boundaries, not excerpts from a nonexistent
Part 2 implementation. Numbered listings preserve exact source indentation;
each selection contains 20–40 source lines. Phase C must add actual parser,
checker, evaluator, relay and receiver tours with measured evidence.

### 4.1 Existing source is stored without a compiler

Source: [services/api/src/warden_api/monitors/router.py](../../services/api/src/warden_api/monitors/router.py), lines 520–543.

```text
 520 |                 detail="min_interval_s out of range",
 521 |             )
 522 |         monitor.min_interval_s = body.min_interval_s
 523 |
 524 |     if body.max_interval_s is not None:
 525 |         if body.max_interval_s < monitor.min_interval_s or body.max_interval_s > 2592000:
 526 |             raise APIError(
 527 |                 status_code=422,
 528 |                 title="Validation Error",
 529 |                 detail="max_interval_s invalid",
 530 |             )
 531 |         monitor.max_interval_s = body.max_interval_s
 532 |
 533 |     if body.state is not None:
 534 |         monitor.state = body.state
 535 |
 536 |     if body.needs_render is not None:
 537 |         monitor.needs_render = body.needs_render
 538 |
 539 |     if body.rule_src is not None:
 540 |         monitor.rule_src = body.rule_src
 541 |
 542 |     if body.selector_config is not None:
 543 |         validate_selector_config(body.selector_config)
```

The update path stores rule source on the monitor and increments the optimistic version afterward. That is an integration point to replace with shared compilation and owner-scoped endpoint validation. A source string in the schema does not establish a language engine. Keep the existing concurrency contract while adding validation, rather than accepting invalid source through an older CRUD route.

### 4.2 Event and notification intent already share a transaction

Source: [services/processor/src/warden_processor/main.py](../../services/processor/src/warden_processor/main.py), lines 121–150.

```text
 121 |             record.snapshot_id = snapshot.id
 122 |             if classification and classification.meaningful and previous is not None:
 123 |                 event = ChangeEvent(
 124 |                     monitor_id=monitor.id,
 125 |                     from_snapshot_id=previous.id,
 126 |                     to_snapshot_id=snapshot.id,
 127 |                     detected_at=now,
 128 |                     kind="structural",
 129 |                     score=classification.score,
 130 |                     meaningful=True,
 131 |                     features=classification.features,
 132 |                     classifier_version=CLASSIFIER_VERSION,
 133 |                 )
 134 |                 session.add(event)
 135 |                 await session.flush()
 136 |                 record.event_id = event.id
 137 |                 payload = ChangeEventPayload(
 138 |                     str(event.id),
 139 |                     str(monitor.id),
 140 |                     str(monitor.user_id),
 141 |                     now.isoformat(),
 142 |                     event.kind,
 143 |                     event.score,
 144 |                 )
 145 |                 session.add(
 146 |                     Outbox(
 147 |                         kind="event",
 148 |                         payload=json.loads(payload.encode()),
 149 |                         idempotency_key=f"event:{event.id}",
 150 |                     )
```

The meaningful classifier result creates a ChangeEvent and flushes for its ID, then stages an event outbox with a stable key. The surrounding process transaction includes snapshot and job receipt. Part 2 belongs at this boundary: build typed context, evaluate, reserve cooldown and stage alert intents before commit. No network send belongs inside this block.

### 4.3 Reuse the outbox ownership contract

Source: [libs/warden_db/src/warden_db/models.py](../../libs/warden_db/src/warden_db/models.py), lines 325–348.

```text
 325 |     __tablename__ = "outbox"
 326 |     __table_args__ = (
 327 |         CheckConstraint("status IN ('pending', 'sent', 'failed')", name="status_valid"),
 328 |         # Partial index: only pending rows need to be polled.
 329 |         # Delivered rows are excluded, keeping the relay's query fast.
 330 |         Index(
 331 |             "ix_outbox_available_at_pending",
 332 |             "available_at",
 333 |             postgresql_where=text("status = 'pending'"),
 334 |         ),
 335 |     )
 336 |
 337 |     id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, primary_key=True)
 338 |     kind: Mapped[str] = mapped_column(Text, nullable=False)
 339 |     payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
 340 |     idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
 341 |     available_at: Mapped[datetime] = mapped_column(
 342 |         DateTime(timezone=True), nullable=False, server_default=text("now()")
 343 |     )
 344 |     attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
 345 |     status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
 346 |     created_at: Mapped[datetime] = mapped_column(
 347 |         DateTime(timezone=True), nullable=False, server_default=text("now()")
 348 |     )
```

The table distinguishes kinds, has a unique intent key, available time and attempt count, and indexes pending work. Existing fetch/event consumers filter their own kinds. Alert claims need additive lease/token and attempt-ledger fields; replacing the table would lose the established shared boundary. A sent flag alone does not resolve the remote acknowledgment ambiguity.

### 4.4 The existing worker claim boundary

Source: [libs/warden_redis/src/warden_redis/worker.py](../../libs/warden_redis/src/warden_redis/worker.py), lines 79–103.

```text
  79 |     """
  80 |     if claim.job is None:
  81 |         await redis.xadd(
  82 |             dead_stream, {"payload": claim.raw, "source": stream, "message_id": claim.message_id}
  83 |         )
  84 |         await ack_job(redis, stream, GROUP, claim.message_id)
  85 |         return
  86 |
  87 |     async def heartbeat() -> None:
  88 |         while True:
  89 |             await asyncio.sleep(max(0.01, idle_ms / 3000))
  90 |             if not await renew_claim(redis, stream, GROUP, consumer, claim.message_id):
  91 |                 _log.warning("claim_ownership_lost", job_id=claim.job.job_id if claim.job else "")
  92 |                 return
  93 |
  94 |     task = asyncio.create_task(heartbeat())
  95 |     started = time.monotonic()
  96 |     try:
  97 |         await handler(claim.job)
  98 |         await ack_job(redis, stream, GROUP, claim.message_id)
  99 |         _log.info("claim_done", job_id=claim.job.job_id, elapsed_s=time.monotonic() - started)
 100 |     finally:
 101 |         task.cancel()
 102 |         with contextlib.suppress(asyncio.CancelledError):
 103 |             await task
```

The heartbeat renews ownership while the handler runs. Acknowledgment follows the handler, so failed processing leaves the message pending. Finally cancels the heartbeat. Permanent missing objects now take a specific failed-receipt path rather than returning forever; temporary failures still retry.

### 4.5 Validation must govern the HTTP connection

Source: [libs/warden_net/src/warden_net/ssrf_check.py](../../libs/warden_net/src/warden_net/ssrf_check.py), lines 113–133.

```text
 113 |     try:
 114 |         resolved = await resolve_host(host)
 115 |     except Exception as exc:
 116 |         return SSRFCheckResult(safe=False, reason=f"dns_resolution_failed: {exc}", resolved_ips=[])
 117 |
 118 |     if not resolved:
 119 |         return SSRFCheckResult(safe=False, reason="no_addresses_resolved", resolved_ips=[])
 120 |
 121 |     resolved_strs = [str(ip) for ip in resolved]
 122 |
 123 |     # Verify every IP
 124 |     for ip in resolved:
 125 |         blocked_reason = is_blocked_ip(ip)
 126 |
 127 |         if blocked_reason is not None:
 128 |             # Information leakage note: Detailed server-side reasons are stored in
 129 |             # this result for internal logs, but external API endpoints must return
 130 |             # generic errors to avoid exposing internal topology.
 131 |             return SSRFCheckResult(safe=False, reason=blocked_reason, resolved_ips=resolved_strs)
 132 |
 133 |     return SSRFCheckResult(safe=True, reason=None, resolved_ips=resolved_strs)
```

All resolved addresses must pass the guard. Its return value is a validation result, not an opened connection. A webhook uses no allowlist and must connect to a validated address while retaining TLS hostname checks. A second independent DNS resolution would reopen the validation gap.

### 4.6 JSON storage does not imply typed extraction

Source: [libs/warden_db/src/warden_db/models.py](../../libs/warden_db/src/warden_db/models.py), lines 290–309.

```text
 290 |         ForeignKey("monitors.id", ondelete="CASCADE"), nullable=False
 291 |     )
 292 |     # NO FKs on from/to snapshot IDs - same reason as base_snapshot_id above:
 293 |     # the snapshots PK is composite, so a single-column FK is not valid.
 294 |     from_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
 295 |     to_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
 296 |     detected_at: Mapped[datetime] = mapped_column(
 297 |         DateTime(timezone=True), nullable=False, server_default=text("now()")
 298 |     )
 299 |     kind: Mapped[str] = mapped_column(Text, nullable=False)
 300 |     score: Mapped[float | None] = mapped_column(Double, nullable=True)
 301 |     meaningful: Mapped[bool] = mapped_column(Boolean, nullable=False)
 302 |     features: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
 303 |     ops: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
 304 |     extracted: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]
 305 |     classifier_version: Mapped[str | None] = mapped_column(Text, nullable=True)
 306 |
 307 |     monitor: Mapped["Monitor"] = relationship(back_populates="change_events")
 308 |     feedback_labels: Mapped[list["FeedbackLabel"]] = relationship(back_populates="change_event")
 309 |
```

The event stores classifier metadata and optional JSON extraction. The processor does not yet populate typed price values. Part 2 must define and persist versioned context before evaluating price predicates. Optional storage is an integration point, not an implemented feature.

## 5. Interview question bank

These answers describe the proposed contract and existing foundations, not a
claim that Part 2 implementations have passed their checkpoints.

### Q01. Why a DSL rather than embedded Python?

A DSL exposes only the operations the product intends to support. Embedded Python
would bring a much larger execution and security surface. Explicit tokens, syntax
and types make the accepted behavior auditable. WARDEN needs policy expressions,
not imports, arbitrary functions or general loops.

### Q02. How do lexing and parsing differ?

Lexing identifies tokens and their source positions from characters. Parsing
groups those tokens into a legal syntax tree. A correctly tokenized expression
can still be missing an operand or closing parenthesis. Separate stages let tests
identify where the first misunderstanding occurred.

### Q03. How does Pratt parsing implement precedence?

Operators receive binding powers and each expression call has a minimum admitted
power. Stronger multiplication can become part of addition's right operand. The
right operand's minimum also determines same-precedence associativity. Parentheses
explicitly override grouping through a nested expression parse.

### Q04. Why reject chained comparisons?

Different languages give them different meanings. A naive left-associative parse
could compare a boolean result with a number. Requiring separate comparisons
joined by `and` avoids that ambiguity. It also simplifies type checking and
canonical printing in v1.

### Q05. What does printer round-trip equality compare?

It compares normalized syntax structure and literal values. Original whitespace
and spans are excluded because formatting changes them. Canonical printing should
also be idempotent. Neither property substitutes for independent intended-meaning
examples.

### Q06. [Gotcha] Can 50,000 round trips prove correct precedence?

No, a parser and printer can share the same wrong precedence. Independent expected
trees and evaluation cases must establish intended semantics. Fuzzing explores
combinations and failure handling rather than proving all possible inputs. Record
its seed, generator limits and distinct outcomes instead of only a large count.

### Q07. Why type-check before running a rule?

It catches unknown fields and wrong function or operator types at save time.
Otherwise the first relevant event discovers an error after alerts appear active.
Runtime checks still handle data-dependent failures such as division by zero.
Both stages need useful diagnostics rather than raw interpreter exceptions.

### Q08. How do spelling suggestions work?

Levenshtein distance counts a minimal sequence of insertions, deletions and
substitutions between names. A bounded comparison against visible declared fields
finds a few likely candidates. The checker suggests rather than silently changing
the policy. It must never reveal other tenants' private names.

### Q09. [Gotcha] What if a price field is absent?

It yields Missing rather than zero or an unknown-name diagnostic. Proposed
comparisons with Missing return false, including not-equal. Boolean negation can
then turn that false into true. Use an explicit `exists` guard when presence is
required, and test the surprising cases directly.

### Q10. Why not derive price from classifier score?

The score estimates meaningfulness, not an item's monetary value. A page also
contains many unrelated numbers. Explicit extraction needs selector, currency and
decimal conventions. Persisting versioned typed context makes later decisions
reproducible.

### Q11. Why use Decimal for price thresholds?

It matches explicitly base-ten threshold conventions better than binary floats.
It still needs bounded precision, magnitude and exception handling. A percentage
drop additionally requires a valid nonzero previous value and matching currency.
Undefined arithmetic must never become a match.

### Q12. [Gotcha] Does an asyncio timeout stop a malicious synchronous loop?

Not if the loop never yields to the event loop. Each work unit must consume steps
and check a monotonic deadline. Source, token, depth, string and numeric bounds
prevent large work from hiding inside a small tree. A subprocess watchdog detects
regressions in the interpreter's own guards.

### Q13. Why RE2 for user regex?

User-controlled patterns applied to untrusted web text create a backtracking
resource-exhaustion risk. RE2 intentionally supports a restricted subset with
stronger execution bounds. Pattern, input and memory limits remain necessary.
Unsupported syntax must fail instead of falling back to Python `re`.

### Q14. Does a history count include the current event?

The proposed contract excludes it and counts prior committed meaningful events
for the same monitor. Persisted processing time and IDs define order and ties.
Exact interval boundaries are part of the contract and tests. This is not a claim
about the website's true publication timestamps.

### Q15. [Gotcha] Why not insert the current event into Redis first?

A later PostgreSQL rollback would leave phantom history. Retrying with unstable
IDs could also inflate the count. The design caches committed prior events and
checks a watermark against durable history. Missing or stale projections are
repaired before their counts are trusted.

### Q16. Where should cooldown state live?

It should commit with the event and scheduled actions. PostgreSQL plus the monitor
lock provides that boundary in this design. Redis-only state cannot roll back
with a failed event transaction. Retrying an existing logical delivery must not
reserve cooldown again.

### Q17. What does the outbox solve?

It commits the business event and send intent together. That closes the crash gap
between saving an event and remembering to send its alert. A relay can retry
committed intents independently. Reuse the existing Outbox with isolated kinds
rather than creating competing ownership.

### Q18. [Gotcha] Does an outbox guarantee exactly-once HTTP delivery?

No, a receiver can commit before its acknowledgment is lost. The sender cannot
know whether another attempt is necessary without cooperation. Stable IDs and
atomic receiver deduplication make repeated attempts harmless for that receiver.
An arbitrary third-party effect cannot become exactly once through sender code
alone.

### Q19. Why use SKIP LOCKED?

It lets concurrent relays select different due rows without waiting behind one
another. That incomplete view is suitable for claiming work rather than ordinary
reporting. The claim transaction records an expiring attempt token. Completion
must still match that token to prevent stale state overwrites.

### Q20. [Gotcha] Why is a lease insufficient to prevent duplicate effects?

Expiry cannot retract an HTTP request already in flight. The old attempt may
finish after another relay reclaims its obligation. Conditional completion protects
sender state but cannot undo a receiver effect. Receiver idempotency remains
necessary.

### Q21. Why poll when LISTEN/NOTIFY exists?

Notifications are wakeups for connected listeners rather than durable jobs.
Disconnects and startup races can miss the useful wakeup opportunity. Commit
LISTEN before the initial scan and rescan after reconnect. Periodic scanning
repairs missed hints while the table remains authoritative.

### Q22. Which bytes should HMAC sign?

Sign the attempt timestamp, stable delivery ID and exact raw body with unambiguous
separators. The receiver verifies those bytes before interpreting JSON. Parsing
and reserializing could change the bytes without changing apparent content.
Constant-time comparison and explicit key versions are separate required details.

### Q23. [Gotcha] Is timestamp freshness sufficient replay protection?

A captured valid request can still replay inside the allowed window. The receiver
must persist its signed-ID receipt with the effect in one transaction. A retry
uses a fresh timestamp and signature while preserving logical ID and body.
Retention and maximum delivery age must be coordinated.

### Q24. Why apply the monitor SSRF guard to webhooks?

Both give a user influence over outbound server connections. Internal and metadata
targets remain dangerous when the request is called an alert. Registration,
retry and manual redelivery each need validation without simulator exceptions.
The connection must use a validated address while TLS verifies the original
hostname.

### Q25. [Gotcha] Can localhost satisfy the live webhook proof?

Not under the explicit policy prohibiting private webhook exceptions. Injected
transport can test application transactions and signatures without a real public
connection. That evidence must be distinguished from DNS/TLS interoperability.
A human-controlled public HTTPS receiver is required for the fully live wire proof.

### Q26. How does redelivery differ from rerunning a rule?

It retries an existing logical intent with preserved identity and body. It must
not reevaluate history or consume cooldown again. Authorization, request
idempotency and audit are still necessary. Intentionally creating another business
notification needs a separately named operation.

### Q27. What does Mailpit prove?

It proves local SMTP capture with expected recipient and content. It does not
establish production inbox placement or human reading. Ambiguous SMTP acknowledgments
can still create duplicate attempts. Delivery records must distinguish these
claims.

### Q28. Why a project subnet instead of changing Docker globally?

A daemon change could affect unrelated projects and require a disruptive restart.
The latest human policy permits an explicit project range checked against actual
routes. The local override limits scope while retaining volumes and host settings.
It must be rechecked after network changes rather than treated as universally safe.

## 6. Try it yourself

Use a temporary directory outside WARDEN, such as one returned by `mktemp -d`.
Use standalone standard-library scripts and fixture data, never production
credentials, database rows, URLs or source imports. Each exercise has an explicit
success condition and fits in less than an hour. Predict the failing case before
running it, then explain why your first implementation did or did not survive.

### 6.1 Build a tiny expression reader — 45–55 minutes

Accept integers, plus, multiplication and parentheses only. First write a cursor
lexer with positions and test an unknown character and missing closing quote in
a deliberately extended string experiment. Next use binding powers to parse the
numeric subset, then print its trees with parentheses. Check two plus three times
four and its explicitly grouped alternative against hand-drawn expected trees.
Generate 100 bounded random trees and check normalized round trips. Success is
correct independent examples, clean rejection of bad input and no infinite loop;
do not claim this scratch parser is the WARDEN compiler.

### 6.2 Add types before values — 30–40 minutes

Represent a tiny tree using tuples and give two fields declared Decimal and
String types. Write one pass that rejects invalid operations without evaluating
them, then a second pass that uses fixture values. Include a wrong type in an
unreachable branch and ensure the checker still reports it. Add an explicit
Missing sentinel and demonstrate how comparison false can become true under not.
Success is explaining the different meanings of unknown name, absent value,
false condition and runtime failure without looking at the script.

### 6.3 Reproduce the outbox crash window — 40–50 minutes

Use a scratch SQLite database with orders and pending-intents tables. Commit an
order and its intent together, then simulate exit before a relay reads it.
Restart and show the intent remains owed. Give the fake receiver a separate
SQLite transaction that inserts receipt ID and increments its effect counter.
Crash after receiver commit but before marking sender success, then resend the
same ID. Success is two attempts and exactly one effect; a receipt inserted in
a separate transaction is the deliberately broken version to compare.

### 6.4 Sign and replay a message without networking — 25–35 minutes

Use an obvious scratch-only test key, raw JSON bytes, a delivery ID and an injected
integer clock. Compute HMAC over the specified fields, compare with compare_digest
and reject a tampered byte or stale timestamp. Show that replaying the unchanged
request inside the freshness window passes signature verification. Add a scratch
SQLite receipt/effect transaction and show it removes the duplicate effect.
Success is explaining why signatures, freshness and deduplication are three
separate checks rather than synonyms. Never reuse the scratch key for deployment.

### 6.5 Model a window and rollback — 35–45 minutes

Keep a scratch committed-event list and a separately rebuildable sorted projection.
Implement count for one monitor and a chosen exact window boundary, excluding
the current tentative event. Simulate a transaction rollback after evaluation;
verify the tentative event never entered the projection. Delete the projection
and rebuild it from committed history, then compare the counts. Finally insert
a tentative event early in a deliberately broken version to see phantom history.
Success is an independent list-based oracle and explicit time/tie tests, not
merely calling the same count function twice.

## 7. Known limitations and what a later part must revisit

- Part 2 is designed only. All CP1–CP4 implementations, migrations, new routes,
  100+ conformance cases, 50k fuzz evidence and alert-delivery proofs remain owed.
- Typed price extraction does not currently exist in the pipeline. It must be
  added explicitly and evaluated on real pages, not only the simulator.
- The resource values in the contract are proposed limits, not measured service
  capacity. Parser limits, Redis repair cost and large history retention need proof.
- One rule per monitor and immutable revision cooldown semantics are intentional
  initial scope limits. More complex rule combinations need a future policy.
- Redis window projections require watermark repair under the monitor lock.
  Naive precommit writes or silent zero on cache loss would violate the design.
- Public DNS/TLS delivery proof requires a human-controlled public receiver and
  human-owned signing credentials. A private URL allowlist is prohibited.
- Existing monitor safe GET resolves again after validation. The new webhook
  POST must pin validated addresses; no complete SSRF containment claim is made.
- Exactly-once network requests are not promised. Reference-receiver deduplication
  proves its own effect behavior, not third-party cooperation or SMTP inbox effects.
- Deployment will need exact human-run credential/ACL setup for the new service;
  no actual ACL, environment or signing key was inspected or modified here.
- The local Docker subnet is host-specific. Daemon automatic pools remain
  uncorrected, and changed VPN/campus routes require another preflight.
- The Part 1 rerun exposed missing-object results that previously retried forever.
  The new narrow terminal classification preserves failed receipts rather than
  inventing snapshots. Missing historical objects cannot be recreated from a hash;
  their provenance and recovery require a separate storage audit.
- Redis dead-letter append and the failed PostgreSQL receipt are separate writes
  in the inherited invalid-result path. The receipt/error is durable, but a crash
  between those operations can omit the Redis copy; do not claim a transactional
  DLQ for that older path. Part 2 delivery failure state will live transactionally
  in PostgreSQL with its attempt ledger.
- The pre-existing uncommitted SSRF diff is preserved independently. A clean clone
  without that working-tree implementation is not the same verified source state.

## 8. Updated docs/AUTHORSHIP.md

The diff below is relative to the ledger at this Phase A session's start. Earlier
uncommitted documentation/provenance is deliberately retained, not reattributed.

```diff
--- a/docs/AUTHORSHIP.md
+++ b/docs/AUTHORSHIP.md
@@ -28,3 +28,7 @@
 | Trusted operator proof ledger and bounded proof cleanup | `libs/warden_db/src/warden_db/proof_ledger.py`, `scripts/chaos_queue_proof.py`, processor integration test, `docs/reports/part-1-live-attempt.json` | Codex AI | Keeps DB credentials inside trusted scheduler. Live S3 permission and WebSocket phases passed; 500-job run interrupted. Final private test audits three snapshots and one event/outbox against receipts; no Gate 2 pass claimed. |
 | Shared report requirements | `docs/REPORT_SPEC.md` | Human requirements, transcribed by Codex AI | Supplied 25 October 2026; nine ordered sections, source excerpts, model answers, scratch exercises and an actual authorship diff. |
 | Part 1 report rebuilt to the shared spec | `docs/reports/part-1.md`, `README.md`, report-spec link in `docs/STANDING_RULES.md` | Codex AI, 25 October 2026 | Documentation-only expansion grounded in current source and recorded evidence; does not change code authorship or claim the interrupted live Gate 2 passed. Docker hold preserved. |
+| Part 2 Phase A language/delivery contract and learning report | `docs/{dsl,PART2_PLAN}.md`, `docs/reports/part-2.md` | Codex AI, 28 October 2026 | Source-grounded design only; precise diagnostics selected as the addition. No CP1–CP4 implementation or fuzz/signature proof claimed; Phase B waits for human go. |
+| Route-checked local Docker recovery | `docker-compose{,.override}.yml`, `docs/{STANDING_RULES,DOCKER_NETWORK_RECOVERY,PART1_SETUP}.md`, README/report status | Human supplied policy and explicit scoped recovery approval; Codex AI implemented | Local override selects 10.200.1.0/24 after inspecting all route tables and Docker subnets. Named volumes retained; no daemon, firewall or host-network settings changed. |
+| Missing-object poison-result recovery | `libs/warden_net/{src/warden_net/objectstore.py,tests/test_objectstore.py}`, `services/processor/{src/warden_processor/main.py,tests/test_durable_effects.py}` | Codex AI, 28 October 2026 | Explicit missing-key errors become failed receipts/quarantine; unrelated valid work proceeds. Auth/transient/bucket failures remain distinct; existing tests retained with new regression coverage. Missing object provenance is not fabricated. |
+| Completed Part 1 live recovery proof | `docs/reports/part-1-{gate2,recovery-2026-10-28}.json`, Part 1/2 reports and runtime handoff | Codex AI ran the proof under explicit human approval | Fresh batch 500/500, zero lost/duplicate durable effects, 12 kills, three shared-counter reclaims, cross-replica 1.287 s. Earlier timeout retained as failure; no region/storage-loss/HA guarantee inferred. |
```

## 9. Handoff

Read [the current combined Handoff](parts-2-3.md#9-handoff) and
[execution status](../PART23_STATUS.md). The human authorized full Parts 2/3
without checkpoint pauses. The compiler, delivery and learning core now exist;
Django/replica-pooler deployment and several live proofs remain incomplete.
The source gate and private proofs are recorded there. Live app containers still
use the previous Part 1 images/schema; new migrations are not deployed.
Do not follow this historical Phase A document's former instruction to wait for go.
