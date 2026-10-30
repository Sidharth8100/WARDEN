# WARDEN: a recall sheet for Prompts 3 and 4

Use this after reading the explanation. Close the reports and answer aloud
before looking at the answers. The examples are illustrative, not measured
project performance.

## The story to reconstruct

WARDEN promises to notice meaningful changes to a monitored website.
Prompt 3 builds the fetch/compare pipeline in one API process. Prompt 4 plans
scheduled work, distributed workers, crash recovery, and live notifications.
The current Prompt 4 report is reconnaissance, not a completed implementation.
Prompt 3 still has four student algorithm groups; classifier fallbacks let
some surrounding behavior work without them.

Draw this from memory:

```text
Prompt 3:
request -> safe fetch -> store bytes -> normalize -> compare -> classify -> DB

Prompt 4, planned:
API / scheduler -> jobs -> fetcher -> results -> processor -> database
                                                   |
                                      event outbox -> event log / live push
                                                   -> API replica -> browser
```

Libraries are shared code. Services are running processes. A container packages
a process and its runtime environment. A replica is another running instance
of the same service. PostgreSQL transactions protect related durable records;
the object store holds large page/screenshot bodies; Redis coordinates cache,
rate, queue, lease, and live-event operations.

## Sixteen questions

1. **Why not compare raw HTML bytes?** Pages contain timestamps, generated
   tokens, scripts, whitespace differences, and other noise. Normalize the
   representation and scope it to the user's interest. Removing information
   can also hide a meaningful change, so normalization rules need tests.
2. **Why two fetch tiers?** Simple HTTP is cheaper. A browser executes JavaScript
   when the interesting content is rendered by scripts. Browsers cost more CPU,
   memory, and operational effort.
3. **Why separate fetch from compare?** Network fetching and deterministic
   comparison have different dependencies, privileges, and failure modes.
   A comparison can be tested on stored input without the live website.
4. **What are the four Prompt 3 algorithm groups for?** Myers explains exact
   text edits; SimHash approximates document similarity; DSU groups neighboring
   changed pixels; TF-IDF compares weighted word composition. They answer
   different questions and none alone defines business importance.
5. **Why keep full snapshots among deltas?** A delta saves space but needs
   previous state. Periodic full keyframes bound reconstruction work. The
   compression helper exists; its presence alone does not prove the live API
   stores compressed deltas.
6. **Why does a tiny edit sometimes matter most?** A one-digit price change may
   be more valuable than a large banner replacement. Classification must match
   the user's purpose, not merely the number of changed bytes.
7. **What is an acknowledgment?** A promise from a consumer that its required
   durable responsibility has been completed. Claiming a job is not that
   promise. In Redis Streams, acknowledgment clears its group's pending entry,
   not necessarily the stream's retained history.
8. **Why can a retry duplicate an effect?** The worker may save successfully
   and crash before acknowledging. Another worker sees unfinished delivery and
   repeats it. Delivery status and business completion are separate facts.
9. **How do we keep one durable effect?** Preserve a stable logical job ID and
   atomically commit its unique receipt with its business writes. A duplicate
   finds the receipt. A receipt saved separately has its own crash gap.
10. **Why an outbox?** A database commit and a Redis publish are separate
    operations. Save the business update and the obligation to publish in one
    database transaction. Retry publishing, accepting deduplicated duplicates.
11. **Why is a lease insufficient?** A paused leader can resume after expiry
    and believe it still has authority. A newer fencing token, enforced by the
    protected database, rejects older authority after the takeover is recorded.
12. **Why subscribe before replay?** Replay-then-subscribe leaves a gap.
    Subscribe first, replay history, then deduplicate buffered overlap using
    stable event identifiers. Expired history requires explicit resync.
13. **What do the nine Prompt 4 modules remember?** Bloom: possible membership;
    timing wheel: due time; budget: crawl allocation; ring: work owner;
    cache: recent reusable values; GCRA: admission time; coordination: live
    membership/current authority; queue: unfinished work; eventlog: event history.
14. **What does atomic mean here?** Competing operations cannot interleave
    halfway through the protected check-and-update. Redis script atomicity
    does not make a separate PostgreSQL operation part of the same transaction.
    Atomicity, disk durability, and permissions are separate properties.
15. **Why remove unused credentials and packages?** It reduces accidental and
    malicious capability. Actual network denial still needs network enforcement;
    an import test or missing HTTP library cannot prove network isolation.
16. **What does a passing test establish?** The tested claim under its stated
    conditions. A mock, synthetic fixture, skipped test, historical result,
    and real crash proof provide different evidence.

## Three failure drills

- A worker acknowledges and then crashes before saving: pending recovery no
  longer knows the job needs work. Acknowledge after the durable handoff/save.
- A worker saves and crashes before acknowledging: delivery repeats. Use a
  durable receipt to avoid applying the same logical effect twice.
- The database saves a change and the process crashes before event publishing:
  a transactionally written event outbox retains the obligation to publish.

For every arrow on your diagram ask: who owns this responsibility, where is it
recorded, and what happens if the process dies immediately after this arrow?

## Reading the reports critically

- Prompt 3's 100% score is a reported historical result on 210 synthetic pairs,
  with no labeled real pairs at report time. The current evaluation script
  invokes the simulator in-process; it is not a complete browser/queue/DB proof.
- Absence of network-client dependencies does not prevent Python sockets.
- Hash equality is practical evidence of equality only for what was correctly
  encoded, subject to collision assumptions; it is not an absolute proof.
- Compression ratios, throughput, and storage-prefix benefits require
  measurements on the actual workload/backend.
- The TF-IDF formula in the student contract differs from the default sklearn
  oracle smoothing. Agree on the mathematical contract before comparing results.
- A queue crash proof needs explicit assumptions: worker restarts and eventual
  recovery are different from total Redis/disk loss or permanently invalid jobs.

## A three-day learning loop

Today: draw both flows and explain a price change from URL to notification.
Tomorrow: reconstruct the three failure drills without notes.
On day three: explain one design choice in six sentences: problem, simplest
alternative, failure/limit, chosen design, cost, and evidence that would test it.

The goal is to reconstruct why a component exists, rather than memorize the
component's name. Checkpoint numbers and package versions are reference details.
