# WARDEN authorship ledger

Git author identity records who committed a change; it does not prove who
composed it. The human describes Prompts 1–3 as AI-assisted. Do not attribute
unverified implementation work to the human merely because commits use their
account. Rows are updated as Part 1 work lands.

| Component | Files | Authored by | Notes |
|---|---|---|---|
| Prompt 1–3 application scaffolding | Existing `libs/`, `services/api`, `sim/chaos_web`, earlier reports | AI-assisted; exact line-level provenance unverified | History commits use Sidharth8100. Original Prompt 3 scaffold commit: `be10eff`. |
| Original Myers, SimHash, DSU/clustering, TF-IDF | `libs/warden_compare/src/warden_compare/{textdiff,simhash,visualdiff,classify}.py` | AI-authored stub scaffolding per earlier reports | At Part 1 start all four groups raised `NotImplementedError`; no human implementation found in their history. Baseline TODO suite: 30 failed, 36 passed, 4 skipped. |
| SSRF IP implementation in working tree | `libs/warden_core/src/warden_core/ssrf.py` | Pre-existing uncommitted work; author unverified | Present before this session: 153 insertions, 47 deletions. Preserve independently of AI changes. |
| Prior learning material and reconnaissance | `docs/learning/*`, `docs/reports/prompt-04.md` | Codex AI, earlier conversation | Learning artifacts and planned architecture, not a completed Gate 2 implementation. |
| Standing rules and Part 1 record | `docs/STANDING_RULES.md`, `docs/AUTHORSHIP.md`, `docs/reports/part-1.md` | Codex AI; rules supplied by human | Current session explicitly authorizes implementation; credential provisioning remains human-owned. |
