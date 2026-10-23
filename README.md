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
passed:** Docker's existing network points to a missing bridge device. Restoring
the human's existing campus-network setup is a prerequisite; agents must not change
Docker daemon/network configuration.

The latest classifier evaluation also exposed insufficient job-list recall. See
the report for actual counts and limitations; historical Gate 1 claims are not
current verification.

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
