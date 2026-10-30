# Docker network recovery record

## Current status — 28 October 2026

The human replaced the earlier blanket hold with a route-checked networking policy,
then explicitly approved scoped `docker compose down` **without `-v`**, rebuild,
start and Part 1 proof. Automatic review had rejected removal under the older
authorization; no removal workaround was used before the new approval.

All route tables, interfaces, existing Docker networks and daemon configuration
were inspected. Ethernet is `10.23.32.0/21` via `10.23.32.1`; Wi-Fi is
`172.31.0.0/18` via `172.31.0.1`. The daemon has only `bip=10.200.0.1/24`, with
no configured automatic address pools. Stale `warden_default` still recorded
forbidden `172.17.0.0/16`. No WARDEN containers were running before recovery.

The local Compose override now explicitly uses **`10.200.1.0/24`**, disjoint from
the inspected Ethernet, Wi-Fi and default bridge (`10.200.0.0/24`). The subnet
and rationale were explained before configuration. Scoped runtime recreation
retained `warden_pgdata`, `warden_redisdata` and `warden_objectdata`. Inspection
confirmed the new project gateway `10.200.1.1`, no 172.17 route, and unchanged
Ethernet/Wi-Fi routes. Images rebuilt and services became healthy/reachable;
real storage/auth/queue connectivity is exercised by the proof rather than
inferred solely from health status. The built-in bridge is unchanged.

No daemon restart/settings, host IP/default gateway/routes/firewall/network-manager
or credential files were edited. Automatic allocation remains unsafe until the
host administrator corrects default address pools. The chosen /24 is based on
today's visible routes, not knowledge of every remote campus/VPN destination;
reinspect before creation on a changed host/network. Existing 23 October evidence
below is retained as history and is superseded as authorization by this section.

The first 28 October proof timed out with 500 batch jobs unfinished. It exposed
older missing-object results starving the processor; a tested narrow missing-key
classification now fails/quarantines those results without clearing queues or
inventing snapshots. All 503 receipts for that proof monitor subsequently
completed (500 batch plus setup/scheduled work). The fresh final proof outcome
is **passed**: 500/500 batch jobs, zero lost/duplicate durable effects, 12 kills,
three reclaims observed through shared counters, and cross-replica 1.287 seconds.
`docs/reports/part-1-gate2.json` records the exact result. Final inspection confirmed
only safe bridge/project subnets and localhost published ports; Ethernet gateway
ping returned two replies with no packet loss, and the public default route still
selects Ethernet. Containers remain running under the new authorization.

The final aggregate failure audit across all monitors records 40 explicit
`missing_snapshot_blob` failures and 37 `fetch_attempts_exhausted` failures.
These are outside the successful fresh 500-ID batch; they remain failed receipts,
not silently deleted or relabeled as successful recovery. Missing historical
object provenance/restoration still needs investigation.

## Historical hold — 23 October 2026

The human chose **“Keep Docker stopped; have the existing host address-pool fix
corrected first”** on 23 October 2026. All WARDEN containers are stopped; named
PostgreSQL, Redis and object-storage volumes remain. The Docker daemon itself was
not stopped or reconfigured by the agent. Do not run Compose up/start/restart,
recreate its bridge, or alter host routes until the human confirms the host fix.

## What was observed

- The default `docker0` interface already uses `10.200.0.0/24`.
- WARDEN's separate Compose bridge was allocated `172.17.0.0/16`.
- Recreating that bridge installed a host route sending `172.17.*.*` destinations
  through Docker. The human reported Ethernet trouble and manually deleted it.
- The observed Ethernet default gateway is `10.23.32.1`; the affected campus
  destination in `172.17.0.0/16` is not identified. Do not claim that the Ethernet
  gateway itself is 172.17, or that every Ethernet destination was diverted.
- Docker still has stale network metadata after a host-side `ip link delete`.
  “Container running” and API `/live` can remain green even when inter-container
  communication has failed. Those are process-liveness signals, not dependency
  reachability proofs.

## Why changing a port cannot fix it

Changing `127.0.0.1:18000` to another published port changes how one application's
traffic enters a container. A subnet route decides where traffic to a whole IP
range goes. The `172.17.0.0/16` route exists independently of the API's port number.
A more-specific subnet route can capture campus traffic that would otherwise use
the default Ethernet route. This is consistent with the reported behavior; the
specific affected campus service still needs confirmation.

The built-in `docker0` bridge and a Compose project's default network are separate
networks. Configuring the former alone does not choose the address range for the
latter. Docker allocates unspecified user-defined network subnets from its default
address pools. See [Docker's subnet-allocation documentation](https://docs.docker.com/engine/network/#subnet-allocation)
and [daemon configuration reference](https://docs.docker.com/reference/cli/dockerd/).

## Human-owned correction to review

Ask the person who established the existing campus fix to check Docker's
**`default-address-pools`**, as well as the existing default-bridge configuration.
The pool must avoid campus, VPN, existing Docker and other relevant routed ranges.
The fact that a range is private, or absent from today's local routing table,
does not prove that the campus/VPN never uses it. No replacement CIDR has been
selected or approved in this session.

The responsible host administrator should preserve all existing daemon settings,
validate their corrected configuration and arrange any required daemon restart.
Before that restart, they should handle the stale 172.17 project network metadata
without deleting data volumes; otherwise Docker may reconstruct the old conflicting
network even though the address pool for new networks has been corrected.
The agent must not edit daemon settings or apply a project-specific subnet under
the standing rules. An explicit new exception and an approved unused CIDR would
be required for the project-specific alternative; the human did not select it.

A changed address pool applies to newly created networks; the stale project's
network must eventually be recreated under the corrected policy. That future
cleanup must retain named volumes (`docker compose down` **without `-v`**), then
verify the newly allocated subnet and route before starting the stack. It must not
blindly recreate 172.17 again. No such cleanup/start is authorized before the host
fix is confirmed. [Docker's down reference](https://docs.docker.com/reference/cli/docker/compose/down/)
explains container/network removal and the separate volume-removal option.

## When the human confirms the host fix

1. Read `docs/STANDING_RULES.md` and the Part 1 Handoff first. Confirm that permission
   to resume includes safe recreation of this stale project network.
2. Inspect the chosen address pool, existing routes and the newly allocated project
   subnet with the human's host-network owner. Preserve their campus settings.
3. Build the updated images, apply the local non-root API UID mapping, and verify
   dependency health and real authenticated login. Do not assume `/live` proves it.
4. Resume the accepted work already on disk, then rerun the complete 500-job proof
   with a fresh proof monitor. The interrupted run is not a Gate 2 pass.
5. Record real snapshot/event/outbox receipt audits, reclaims and cross-replica
   delivery. Update the report/authorship ledger and finish with `make check`.

Do not delete volumes, credentials or the pre-existing SSRF working-tree changes.
