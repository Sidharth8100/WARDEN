# WARDEN rule language v1 — implementation contract

Syntax, checking and interpretation are implemented in `libs/warden_rules`.
Delivery integration and scoped proofs are described in
[the combined Parts 2/3 report](reports/parts-2-3.md). Updated deployment and the
remaining live proofs are pending; see that report's Handoff.

## A complete example

```text
when price.drop_percent >= 10
  and price.current < 1000
  and count(changes, 24h) < 3
then webhook("price-alerts") + tag("bargain")
cooldown 6h
```

This means: on a newly classified meaningful ChangeEvent for this monitor,
if the extracted price has dropped at least ten percent, its current value
is below 1000 in the monitor's configured currency, and fewer than three prior
meaningful changes occurred in the preceding day, schedule a webhook and add
the tag. Suppress subsequent matches of this rule revision for six hours.

`price-alerts` names an endpoint owned by the current tenant. It is not a URL,
and the rule contains no webhook secret. Endpoint creation/update APIs receive
URLs, validate them through `warden_net`, and store versioned destinations.
The dispatcher revalidates on every attempt without test-host allowlists.

## Lexical rules and grammar

Input is UTF-8 text limited to 16 KiB. Tokens carry half-open Unicode code-point
offsets and one-based line/column positions. ASCII identifiers and case-sensitive
keywords avoid confusing lookalike names. Whitespace/newlines separate tokens;
`#` starts a comment to end of line outside a string. Strings use JSON-style
double quotes and escapes; reject invalid escapes, unpaired surrogates and raw
control characters. Decimal literals have at most 18 significant digits and six
fractional places, with no exponent, NaN or infinity. Unary minus is an operator,
not part of a numeric token. A duration is a positive integer immediately followed
by `s`, `m`, `h` or `d`; a percentage is an ordinary decimal with an explicit
field name such as `drop_percent`, not a second numeric unit.

```text
program    := "when" expression "then" action ("+" action)*
              ["cooldown" DURATION] EOF
action     := ("webhook" | "email" | "tag") "(" STRING ")"
expression := Pratt expression using the table below
atom       := NUMBER | STRING | "true" | "false" | FIELD
            | "(" expression ")" | function
function   := "exists" "(" FIELD ")"
            | "contains" "(" expression "," expression ")"
            | "matches" "(" expression "," STRING ")"
            | "count" "(" "changes" "," DURATION ")"
FIELD      := IDENT ("." IDENT)*
```

No assignments, loops, recursion, arbitrary function calls, attribute access,
imports, comprehensions, indexing, Python execution or interpolation. `FIELD`
is a lookup in a declared schema, never `getattr` on an application object.
`changes` is a special count-source token, not a general list or free variable.

| Precedence, low to high | Operators | Associativity |
|---|---|---|
| 10 | `or` | left |
| 20 | `and` | left |
| 30 | `== != < <= > >=` | non-associative; reject chained comparisons |
| 40 | `+ -` | left |
| 50 | `* /` | left |
| 60 | prefix `not -` | right/nesting |
| atom | calls, field references, parentheses | explicit grammar |

`not` binds tightly: write `not (score > 0.5)` for a negated comparison.
`+` in the action list is an action separator after `then`, not string addition.
Full input consumption is mandatory; trailing junk cannot be silently ignored.

The hand-built lexer uses character states and explicit cursor advancement.
The Pratt parser's prefix and infix handlers use binding powers from this table.
The pretty-printer emits canonical decimals, JSON strings and sufficient
parentheses to preserve structure. Round-trip equality compares normalized AST
structure while ignoring source spans/comments; formatting is idempotent too.
Round-trip checks alone are insufficient, so precedence has independent expected
AST cases and evaluator examples.

## Types and fields

Core types: Bool, Decimal, String, Duration and Optional[T]. Count returns a
bounded integer represented within Decimal arithmetic; there are no implicit
string/number or truthiness conversions. Duration is accepted only in declared
duration positions. Equality requires compatible types; ordered comparisons
require Decimal operands. Arithmetic accepts Decimal only. Functions have
fixed signatures and arity; `matches` requires a compile-time string pattern.

| Field | Type | Meaning |
|---|---|---|
| `event.kind` | String | classifier event category |
| `event.score` | Optional[Decimal] | finite classifier score, if available |
| `event.meaningful` | Bool | true on the normal alert-evaluation path |
| `text.before`, `text.after` | String | bounded normalized snapshot text |
| `price.previous`, `price.current` | Optional[Decimal] | explicitly extracted values |
| `price.drop_percent` | Optional[Decimal] | `100 * (previous-current) / previous` |
| `price.currency` | Optional[String] | monitor-configured currency code |

Only explicit monitor extraction configuration produces price fields. Extend
the existing selector configuration with one bounded single-value price selector,
currency and a declared decimal/grouping convention; never guess a price from
the first number in a page. Require exactly one selected price value; otherwise
mark it absent and record an extraction reason. Parse with `decimal.Decimal`,
not float; reject nonfinite, negative, oversized and ambiguous values. A zero
previous price or currency mismatch makes the percentage absent. Persist the
typed before/after extraction and schema version with the event for repeatability.
Reuse installed DOM dependencies in a pure helper; do not add network imports
to `warden_compare` or run browser/network activity from the rule library.

Arithmetic involving an absent optional value produces Missing. Comparisons
with Missing, including `!=`, produce false; boolean negation still negates that
false. Therefore authors should use `exists(price.current)` when presence is a
business requirement, especially under `not`. `exists` tests field availability,
not truthiness: an extracted zero exists. Unknown field names are compile errors,
not Missing. Wrong operand types are compile errors even in an unreachable arm.
Division by zero and decimal overflow produce a bounded evaluation error, never
a match; short-circuiting can avoid an operation at runtime.

Unknown names get bounded Levenshtein suggestions from the fixed visible field
schema only. Never suggest another tenant's endpoint, a private object attribute
or a field outside the caller's permitted schema. At most three candidates with
edit distance at most two, sorted by distance and lexicographically.

## Functions, event time and cooldown

- `contains(String, String) -> Bool`: case-sensitive substring containment.
- `matches(String, literal-pattern) -> Bool`: RE2 search semantics, not full match.
  Compile once during validation, bound pattern length/memory and input size,
  reject unsupported constructs. Never fall back to Python `re` on failure.
- `exists(FIELD) -> Bool`: availability of a known declared field.
- `count(changes, DURATION) -> Decimal`: count prior committed meaningful events
  for the same monitor in `[current.detected_at-duration, current.detected_at]`,
  excluding the current event. Same-time events use their durable event IDs as
  the tiebreaker: only earlier events in this monitor's serialized processing
  order are eligible. Duration must be between one second and seven days.

The processor already locks the monitor while producing an event. Evaluate its
rule in that transaction, under the same ordering, against immutable current
inputs and the committed prior history. Redis backs window lookup with bounded
exact count aggregates, identified by current event/time and a durable prior-event
watermark. Missing/stale/malformed counts are reconstructed from PostgreSQL under
the monitor lock, excluding the current event. This replaces the Phase A proposed
sorted set: the deployed GET/SET ACL is sufficient and no credential-file edits
are needed. Cache size depends on distinct windows, not event-history length.
If Redis cannot supply/repair a consistent projection within the deadline, retry
processing; never substitute zero or silently truncate a count. History indexes
and retention must support the full maximum window.

Redis is replaceable projection state, not a second membership authority. Repair
uses committed prior history, so later rule/DB rollback cannot insert a phantom
current event. A new observation can require a new indexed SQL count; this design
does not claim Redis-only window maintenance or no database work per event.
Dry-run evaluation uses an isolated historical adapter, never production window
keys or cooldown state. Tests must prove parity and rebuild/restart behavior.

Cooldown state is durable PostgreSQL state keyed by rule revision and monitor.
Check and update it in the event transaction, with the monitor lock; rollbacks
must undo cooldown consumption. A match reserves cooldown when actions are
scheduled, not when a receiver eventually answers. The first match is allowed;
the exact end boundary is allowed. No clause means zero cooldown. A new revision
starts a new cooldown history; show that behavior in the editing API/UI.

## Actions and ownership

`webhook("name")` and `email("name")` refer to enabled, tenant-owned registered
destinations. `tag("name")` applies a bounded literal tag to this monitor with
an idempotent association insert. Maximum eight actions, no duplicate normalized
actions, tag name maximum 64 code points. No network I/O occurs in the event
transaction. Tag effects and action intents commit together with the event.

Use existing `Outbox` rows with new isolated kinds such as `alert.webhook` and
`alert.email`. A logical delivery ID is deterministic over event ID, immutable
rule revision and action index; outbox idempotency keys remain unique. Snapshot
destination version and canonical body bytes for reproducible retries, with no
secret in the payload. Record non-match, cooldown suppression and evaluation
errors distinctly; do not report all three as successful deliveries.

Use one active rule per monitor initially, with immutable revisions and the
existing `Monitor.rule_src` kept as a compatibility mirror. Endpoints and rules
are owner-scoped in every lookup, including dry-run, validation, delivery logs
and manual redelivery. Existing stored `rule_src` is untrusted: validate before
activation, preserving invalid source for correction instead of executing it.

## Resource envelope and diagnostics

Proposed defaults to verify at CP1/CP2: 16 KiB source, 4096 tokens, 2048 AST nodes,
64 nesting levels, 10,000 evaluation steps, eight actions, 512-byte RE2 pattern,
64 KiB text input per side and a 100 ms evaluation deadline. Decimal intermediate
precision is 28 digits, with explicit maximum magnitude and trapped exceptional
arithmetic; strings have length limits before concatenation could occur (string
concatenation itself is unsupported). Lexer/parser limits apply before recursion
could hit Python's stack limit. Reject oversize text contexts rather than silently
truncating away a matching keyword. CPU steps check a monotonic deadline; async
Redis access also has an outer timeout. Cancelling an await alone cannot interrupt
a synchronous infinite loop, which is why all loops and work units are bounded.

Diagnostics include stable code, human message, half-open span, line/column,
expected token/type and bounded suggestions. Invalid rules return HTTP 422;
bad ownership returns the existing generic authorization/not-found response.
Never include secret-bearing endpoint URLs or arbitrary internal exceptions.
This is the selected worthwhile addition: precise diagnostics improve editing,
testing and a future frontend without exposing interpreter internals to users.

## Validation and release gates

CP1: at least 120 independent conformance cases, independent precedence examples,
normalized AST round trips, pretty-print idempotence and at least 50,000 seeded
generated/mutated programs. Valid programs must parse; invalid ones may return
documented diagnostics only. Unexpected exceptions, hangs or resource-limit
violations fail the run. Use a subprocess watchdog as well as internal budgets,
report counts/seed/time and save a minimized counterexample on failure.

CP2: type errors/suggestions, RE2 unsupported patterns, all Missing paths,
short-circuit behavior, decimal boundaries, clock boundaries, private real Redis
window replay/repair/rollback and concurrent durable cooldown tests. Repeat
recovery after a cache flush; ensure no user can access another user's history.

CP3/4: see [the implementation plan](PART2_PLAN.md) for delivery leases, HMAC,
SSRF, receiver deduplication, retries, Mailpit and durable manual-redelivery
semantics. No stage is complete until its actual evidence is recorded.
