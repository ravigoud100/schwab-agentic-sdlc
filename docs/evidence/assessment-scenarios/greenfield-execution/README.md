# Greenfield scenario: completed execution evidence

**Status: `GREENFIELD_RESULTS_APPROVED`** — workflow `de438ac8-6252-4e90-8b4c-9a6063acfd83`

This directory is a complete archive of `/private/tmp/schwab-assessment-greenfield-success/`
copied verbatim on 2026-10-16. No file was edited during or after the copy.

---

## SQLite lineage (audit.db — 11 events)

| # | Event | Agent | Timestamp (UTC) |
|---|---|---|---|
| 1 | `WORKFLOW_STARTED` | `greenfield_demo` | 2026-10-17T02:01:52 |
| 2 | `GREENFIELD_PLAN_APPROVED` | `greenfield_demo` | 2026-10-17T02:01:52 |
| 3 | `POLICY_EVALUATED` (pre-write, allow) | `policy_engine` | 2026-10-17T02:01:56 |
| 4 | `FILE_CHECKPOINT_CREATED` | `implementation_executor` | 2026-10-17T02:01:56 |
| 5 | `TRANSACTION_APPLIED` | `implementation_executor` | 2026-10-17T02:01:56 |
| 6 | `GREENFIELD_SOURCE_APPLIED` | `assessment_scenarios` | 2026-10-17T02:01:56 |
| 7 | `POLICY_EVALUATED` (test pre-write, allow) | `policy_engine` | 2026-10-17T02:01:56 |
| 8 | `TEST_SCOPE_APPROVED` | `greenfield_demo` | 2026-10-17T02:01:56 |
| 9 | `TEST_FILE_CREATED` | `greenfield_demo` | 2026-10-17T02:01:56 |
| 10 | `WORKFLOW_FINISHED` (`AWAITING_GREENFIELD_RESULTS_REVIEW`) | `greenfield_demo` | 2026-10-17T02:02:00 |
| 11 | `GREENFIELD_RESULTS_APPROVED` | `greenfield_results_review` | 2026-10-17T02:07:07 |

Event 11 is the hash-bound `GREENFIELD_RESULTS_APPROVED` decision from `results-review.db`:
`bundle_hash = dc5c1352ca60063282c90aeae3e96bedbbb06c4ff2d1822c00ab9c9458be9f97`

---

## Generated files and hash verification

| File | SHA-256 | Provenance | Audit event |
|---|---|---|---|
| `UrlHealthComponent.java` | `76f101bea202515f901eb69003109fda7f26ba3a7e1a881ffca2e70371c98a35` | Real Ollama `qwen2.5-coder:latest` | Event 6 |
| `UrlHealthComponentTest.java` | `ba9b1cb2e27b54933c00c7988c0c69b4587b0bf4de4ec41c0cc1457f1e59a1e2` | Vetted deterministic renderer | Event 9 |

Both hashes match the corresponding `proposed_hash` values in the `POLICY_EVALUATED` events (3, 7)
and the `source_hash`/`sha256` in events 6 and 9.

---

## Maven validation

- **Command:** `./mvnw -o -Dmaven.repo.local=/private/tmp/schwab-test-generation/maven-cache clean test`
- **Workspace:** `workspace/url-shortener/`
- **Exit code:** 0
- **Tests:** 9 passed (8 original + `UrlHealthComponentTest`) — zero failures, errors, or skips
- **Log:** [maven.log](maven.log)

---

## Governance and limits

- Proposal hash `a0c5a874b05bb820e65ccef8a45124d34f51181a4233e4d71e3292ad1000aae8` matches the reviewed packet.
- Pre-write policy checks (12 policies) returned `allow` for both source and test files.
- Source files changed only the isolated workspace — **production checkout was not modified**.
- `release_authorized: false` — no merge, release, or deployment occurred or is authorized.
- The archived workspace is assessment evidence only; it is not a production-ready deployment.
