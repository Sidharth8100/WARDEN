# WARDEN standing rules

These rules apply to every session. Read them before making changes.

1. Follow the human's 28 October Docker networking rules below. Docker is allowed
   for normal development/tests, but never use `172.17.0.0/16` or any subnet
   within it, overlap required host networks, use `network_mode: host`, privileged
   containers or `cap_add`. Never change host Ethernet/Wi-Fi/VPN routes to fit Docker.
2. Publish local ports on `127.0.0.1` only. The real Part 5 production deployment
   is a deliberate, separately reasoned public IP/domain exception.
3. Ask before destructive commands: `docker compose down -v`, `docker volume rm`,
   `docker system prune`, `rm -rf` outside this session's build artifacts,
   `git push --force`, `git reset --hard`, `terraform destroy`, or cloud deletion.
4. Never open, print, or edit `.env` or committed credential files, including
   `redis/users.acl`, `seaweedfs/s3config.json`, service-account keys, and PEM/key
   files. When new credentials are needed, stop and give the human exact setup
   values/commands; never generate and silently store secrets.
5. Add only dependencies explicitly listed in the current part's prompt, using
   `uv add`. Ask with a reason before any other third-party dependency.
6. Never weaken mypy strictness, disable/ignore a ruff rule, or delete/loosen a
   failing test to obtain a pass. Fix root causes.
7. Trust-zone boundaries are permanent: fetcher never gains PostgreSQL access;
   `warden_compare` never gains a network-capable dependency. Flag conflicting
   later requirements rather than proceeding.
8. `make check` must be green at session end.
9. Every session updates `docs/AUTHORSHIP.md` and this part's full report, including
   a Handoff that a future session can read without conversation context.

Report structure and minimums are recorded in [REPORT_SPEC.md](REPORT_SPEC.md).
Use its nine ordered sections for every part.

## Current session authorization

On 23 October 2026 the human retired the student-stub system and authorized AI
implementation of all algorithms. The no-checkpoint exception applied to Part 1.
On 28 October the human requested Part 2 Phase A and resuming unfinished Part 1
tests under the new networking policy. Phase A means inspect actual source/tests,
write findings and the plan, then STOP and wait for "go" before Part 2 Phase B.
Part 2 checkpoints require "continue": CP1 lexer/Pratt parser/pretty-printer plus
50k round-trip fuzz cases; CP2 type checker/evaluator/windows/cooldowns; CP3
outbox/signed webhook recovery; CP4 Mailpit/delivery log/manual redelivery.
Phase C is the full report. No stub system is reinstated.

The later 28 October request explicitly authorizes **all of Parts 2 and 3 without
Phase/CP approval pauses** and asks for no approval messages while the human sleeps.
Continue through independent implementation and proofs. The combined report is
`docs/reports/parts-2-3.md`, with detailed direct explanations, no analogies and
no repeated Part 1 material. That request supersedes the checkpoint pauses above,
not secret ownership, strict checks, SSRF or permanent trust boundaries. Do not
generate/store new deployment credentials; prepare human-run setup where needed
and continue unaffected work without sending approval questions.

## Docker networking policy — 28 October 2026 (supersedes earlier blanket hold)

- Before creating any network inspect `ip route`, all relevant route tables,
  `docker network ls`, `docker network inspect`, and `/etc/docker/daemon.json`.
- Automatic allocation is permitted only after verifying the daemon's pools
  exclude conflicting ranges. A safe built-in bridge `bip` alone is insufficient.
- If needed, choose an explicit Compose subnet from actual route/network evidence
  and explain its compatibility before changing configuration. Recheck on another
  host or after a VPN/network change; a private CIDR is not inherently unused.
- Preserve existing daemon settings. Inform the human before any daemon restart
  or disruption of running containers. Never remove networks indiscriminately,
  change host IP/gateway/routes/firewall/network-manager, or use host networking.
- After a network change verify bridge/project subnets, required host routes and
  actual inter-service connectivity. If safety cannot be established, stop and
  report the conflict rather than guessing. Keep volumes and credentials intact.
- Current inspected local choice: `10.200.1.0/24` in the development override.
  It is disjoint from Ethernet `10.23.32.0/21`, Wi-Fi `172.31.0.0/18`, Docker's
  `10.200.0.0/24`, and forbidden `172.17.0.0/16`. The daemon currently has only
  `bip=10.200.0.1/24`; its automatic address pools remain uncorrected.

## One-time recovery authorization — 23 October 2026

After the missing bridge was explained, the human preferred a port change but
explicitly said “if not go ahead.” Since port selection cannot restore a missing
bridge, this authorized one limited recovery: `docker compose down` without `-v`,
then `docker compose up -d`, recreating only WARDEN's containers/default network.
The four attached containers belonged to WARDEN; named `pgdata`, `redisdata` and
`objectdata` volumes were retained. Daemon settings, subnet configuration and
published-port definitions were not edited. This is a one-time exception for the
stale runtime network, not permission for future network configuration changes.

The attempted recreation restored Docker connectivity but reintroduced the
172.17.0.0/16 route and the human's Ethernet problem. The human deleted that bridge
and explicitly selected keeping Docker stopped until the host address-pool fix is
corrected. That later instruction closes the one-time recovery exception: do not
repeat network recreation or start containers under that old authorization.
The 28 October policy above now authorizes a route-checked project-specific
recovery and resumption of Part 1 tests; it does not authorize blindly recreating
the stale network. See `docs/DOCKER_NETWORK_RECOVERY.md` for dated evidence.
