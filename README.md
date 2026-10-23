# WARDEN

WARDEN monitors websites for meaningful changes. The API accepts checks durably,
a scheduler allocates crawl capacity, fetchers retrieve content, processors compare
it, and authenticated WebSockets deliver retained events across API replicas.

Start with [the Part 1 engineering and learning report](docs/reports/part-1.md),
[standing rules](docs/STANDING_RULES.md), and [authorship ledger](docs/AUTHORSHIP.md).
Section 7 of the report gives a beginner learning path and interview exercises.

## Current verification status

The implementation, strict typing, default tests, private PostgreSQL/Redis protocol
tests and Docker builds pass. **The live Gate 2 crash/cross-replica proof has not
passed:** the run was interrupted when the human deleted a conflicting
172.17 bridge to restore Ethernet. All WARDEN containers are now stopped at the
human's request. The host address-pool fix must be corrected before any restart.
See [the network recovery handoff](docs/DOCKER_NETWORK_RECOVERY.md).

Classifier v2 fixes a discovered job-list recall regression and passes 210 seeded
synthetic pairs. No labeled real website evaluation exists yet. See the report for
actual counts, scope and limitations.

## Local checks

```bash
make check
make check-todos
uv run pytest services/processor/tests/test_durable_effects.py libs/warden_redis/tests -m integration -q
uv run python scripts/benchmark_part1.py --output docs/reports/part-1-numbers.json
```

The explicit integration fixtures need local Redis and PostgreSQL 16 binaries and
private Unix-socket access. They do not use deployment credentials or expose TCP
ports. Legacy API integration cases require an explicitly configured test database.

Human-owned deployment provisioning and the live proof command are in
[PART1_SETUP.md](docs/PART1_SETUP.md). The human has already confirmed credentials
ready for this checkout. Never read/print credential files or run the provisioning
script as an agent. Never delete volumes to fix startup problems.

This working tree includes a pre-existing, uncommitted SSRF implementation owned
separately from the Part 1 commits. The report's handoff identifies it; preserve it.
