# WARDEN standing rules

These rules apply to every session. Read them before making changes.

1. Never touch Docker daemon/network config: no custom subnets,
   `network_mode: host`, privileged containers, or `cap_add`. A prior host-level
   campus-network port collision fix depends on this remaining untouched.
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

## Current session authorization

On 23 October 2026 the human retired the student-stub system and authorized AI
implementation of all remaining algorithms, including incomplete earlier work.
The latest request explicitly authorizes the full Part 1 without Phase A or CP
pauses. This overrides the attachment's checkpoint pauses, but does not waive
the credential, dependency, safety, trust-zone, or evidence requirements above.
