# Agentic SDLC Assessment — Final Submission Report

**Project:** Charles Schwab Agentic SDLC — URL Shortener
**Repository:** https://github.com/ravigoud100/schwab-agentic-sdlc
**Branch:** `feature/assessment-improvements`
**Submitted:** 2026-10-17
**Release authorized:** ❌ No — assessment evidence only

---

## Executive summary

This repository demonstrates a complete Agentic SDLC workflow for a Java 21 Spring Boot URL
shortener service. The agent orchestrator (Python 3.12, LangGraph, SQLite) autonomously performs
requirements clarification, planning, code generation, testing, rollback, and policy enforcement
with human approval gates at every significant decision point.

Three distinct assessment scenarios were executed and their evidence is preserved in this repository, including both the historical brownfield retry exhaustion record and a fresh autonomous brownfield execution:

| Scenario | Outcome | Evidence |
|---|---|---|
| **Greenfield** | `GREENFIELD_RESULTS_APPROVED` | `docs/evidence/assessment-scenarios/greenfield-execution/` |
| **Brownfield (Historical)** | `STOPPED_RETRY_EXHAUSTED` + Option A finalization | `docs/evidence/assessment-scenarios/brownfield-final-report/` |
| **Brownfield (Autonomous)** | `BROWNFIELD_RESULTS_APPROVED` (11 tests pass, Attempt 1) | `docs/evidence/assessment-scenarios/brownfield-autonomous-run/` |
| **Ambiguous** | `AMBIGUOUS_RESULTS_APPROVED` (verification-only) | `docs/evidence/assessment-scenarios/ambiguous-execution/` |

---

## Test results (verified from fresh GitHub clone)

### Python orchestrator (485 tests, Java-free, offline)

```
485 passed in 4.52s
```

- Runtime: Python 3.12, LangGraph 1.2.15, offline mock LLM provider
- Test files: 40 (full suite, including new Java import diagnostics regression tests)
- Command: `python -m pytest agent-orchestrator -q`

### Java URL shortener (8 tests)

```
Tests run: 8, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS — 3.663s
```

- Runtime: Java 21.0.12.1 (Temurin), Spring Boot 4.0.8, H2 in-memory DB
- Test classes: `UrlExpirationIntegrationTests` (7) + `UrlShortenerApplicationTests` (1)
- Command: `./mvnw --batch-mode clean test`

Both suites were re-run from a **fresh `git clone`** of the GitHub repository to confirm
repeatability without local state or IDE dependencies.

---

## Scenario detail

### Greenfield (workflow `de438ac8`)

The agent received a new feature requirement (`UrlHealthComponent`), generated Java source using
Ollama `qwen2.5-coder:latest`, applied it through the transactional source engine, ran Maven, and
recorded `GREENFIELD_RESULTS_APPROVED` with bundle hash `dc5c1352…`.

Evidence includes: Ollama prompt/response, source hashes, Maven log, 9-test Surefire output,
SQLite audit trail (11 events), and `results-review.json`.

### Brownfield — Historical retry exhaustion (workflow `e4e0d578`)

The agent attempted to modify an existing file. After 3 attempts exhausted the retry budget
(a missing `import` in generated code caused each compilation failure), the workflow ended
`STOPPED_RETRY_EXHAUSTED`. The attempt budget was never reset.

**Option A resolution**: The offline-assisted validation (11 Maven tests passing) was recorded
with documented architectural gaps. The historical failure, attempt counts, and retry counters
are preserved intact in the audit trail.

### Brownfield — Fresh autonomous execution (workflow `fcd9b8af`)

Following diagnosis and the addition of conservative, read-only pre-Maven Java import diagnostics
and explicit per-file import inventories, a fresh approval-bound workflow was authorized.

- **Generation:** Real local Ollama `qwen2.5-coder:latest` generated complete replacement Java files.
- **Import Diagnostics:** Pre-Maven evaluation verified all common type references resolved cleanly.
- **Architecture Contract:** `UrlService.getStatus(shortCode)` returns `Map<String, Object>` with
  `shortCode` and `expired`, annotated `@Transactional(readOnly = true)`, throwing `ResponseStatusException(HttpStatus.NOT_FOUND)`
  on unknown codes and never incrementing click count. `UrlController` delegates directly.
- **Validation:** Genuine Maven validation passed on **Attempt 1 of 3** (11 tests pass: 7 expiration,
  3 status integration, 1 context loads; 0 failures, 0 errors, 0 skipped).
- **Approval:** Explicit human approval recorded through the `BROWNFIELD_RESULTS_REVIEW` interrupt
  (`BROWNFIELD_RESULTS_APPROVED`, bundle hash `abf8d6df…`). Evidence preserved in
  `docs/evidence/assessment-scenarios/brownfield-autonomous-run/`.

### Ambiguous — "Custom expiration timestamp" (workflow `06a48ae8`)

The requirement "How is expiration selected?" was ambiguous. The agent surfaced two options;
the human selected **"Custom expiration timestamp"** (not "Fixed 24-hour TTL"). This decision
is hash-bound in SQLite (`CLARIFICATION_APPROVED`, req hash `1de7e944…`).

**Verification-only outcome**: After analyzing the codebase, the agent determined that the
selected decision was already fully implemented:
- `UrlController` accepts optional `Instant expiresAt`
- `UrlService` rejects past/equal values (HTTP 400), stores future values, accepts null
- `ShortUrl.isExpired()` enforces expiry at redirect time (HTTP 410)

No code was generated, modified, or deleted. 8 Maven tests confirmed all required behaviors.
This is a valid agentic SDLC outcome: analysis determined no change was necessary.

---

## Architecture

| Layer | Technology |
|---|---|
| Agent orchestrator | Python 3.12, LangGraph 1.2.15, LangChain |
| LLM (live) | Ollama `qwen2.5-coder:latest` (local) |
| LLM (CI/tests) | Mock provider — zero Ollama dependency |
| Checkpoints | SQLite via `langgraph-checkpoint-sqlite` |
| Audit trail | SQLite (`audit.db`) — append-only event log |
| Target service | Java 21, Spring Boot 4.0.8, Maven, H2 (test) |
| Human gates | LangGraph `interrupt()` on plan, test scope, results review |
| Policy engine | 12-policy pre-write checker (SEC, COM, GOV, TEST namespaces) |
| Rollback | File-level checkpoint + transactional revert on Maven failure |

---

## Human approval gates recorded

| Gate | Workflow | Hash-bound event | Status |
|---|---|---|---|
| Requirements clarification | `06a48ae8` | `CLARIFICATION_APPROVED` (`1de7e944…`) | ✅ Recorded |
| Greenfield plan | `de438ac8` | `GREENFIELD_PLAN_APPROVED` | ✅ Recorded |
| Greenfield test scope | `de438ac8` | `TEST_SCOPE_APPROVED` | ✅ Recorded |
| Greenfield results | `de438ac8` | `GREENFIELD_RESULTS_APPROVED` (`dc5c1352…`) | ✅ Recorded |
| Ambiguous plan | `06a48ae8` | `PLAN_CREATED` (`e3726f8d…`) | ✅ Recorded; verification executed |
| Brownfield historical attempts 1–3 | `e4e0d578` | 3 × `ATTEMPT_FAILED` | ✅ Preserved intact |
| Brownfield execution authorization | `fcd9b8af` | `EXECUTION_APPROVED` (`bcdf4c5f…`) | ✅ Recorded |
| Brownfield results (autonomous) | `fcd9b8af` | `BROWNFIELD_RESULTS_APPROVED` (`abf8d6df…`) | ✅ Recorded |
| Ambiguous results | `06a48ae8` | `AMBIGUOUS_RESULTS_APPROVED` (`224c4285…`) | ✅ Recorded |

---

## CI pipeline

`.github/workflows/ci.yml` — triggers on push to `main` or `feature/assessment-improvements`
and on PR to `main`:

| Job | Runs | Passes |
|---|---|---|
| `Python 3.12 Tests` | 485 tests (full suite) | ✅ |
| `Java 21 Maven Tests` | 8 tests | ✅ |

No Ollama, no external services, no production credentials required in CI.

---

## Fresh-clone verification

| Check | Result |
|---|---|
| `git clone` from GitHub | ✅ Exit 0 |
| Python 485 tests | ✅ `485 passed in 4.52s` |
| Java 8 tests | ✅ `BUILD SUCCESS in 3.663s` |
| Evidence directories present | ✅ All scenario dirs cloned |
| No production files modified | ✅ Confirmed by pre/post-run SHA-256 inventory |
| No secrets in repository | ✅ Scan found only intentional test fixture patterns |

---

## What is NOT included

- ❌ No release, merge to `main`, or deployment
- ❌ No reset of historical workflow attempt budgets (`STOPPED_RETRY_EXHAUSTED` preserved)
- ❌ No fixed 24-hour TTL (clarification selected "Custom expiration timestamp")
- ❌ No production credentials or Ollama API keys in the repository
