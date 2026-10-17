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

Three distinct assessment scenarios were executed and their evidence is preserved in this repository:

| Scenario | Outcome | Evidence |
|---|---|---|
| **Greenfield** | `GREENFIELD_RESULTS_APPROVED` | `docs/evidence/assessment-scenarios/greenfield-execution/` |
| **Brownfield** | `STOPPED_RETRY_EXHAUSTED` + Option A finalization | `docs/evidence/assessment-scenarios/brownfield-final-report/` |
| **Ambiguous** | `AMBIGUOUS_RESULTS_APPROVED` (verification-only) | `docs/evidence/assessment-scenarios/ambiguous-execution/` |

---

## Test results (verified from fresh GitHub clone)

### Python orchestrator (476 tests, Java-free, offline)

```
476 passed in 4.87s
```

- Runtime: Python 3.12, LangGraph 1.2.15, offline mock LLM provider
- Test files: 39 (full suite, not a subset)
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

### Brownfield (workflow `e4e0d578`)

The agent attempted to modify an existing file. After 3 attempts exhausted the retry budget
(a missing `import` in generated code caused each compilation failure), the workflow ended
`STOPPED_RETRY_EXHAUSTED`. The attempt budget was never reset.

**Option A resolution**: The offline-assisted validation (11 Maven tests passing) was recorded
with documented architectural gaps. The historical failure, attempt counts, and retry counters
are preserved intact in the audit trail.

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
| Brownfield attempts 1–3 | `e4e0d578` | 3 × `ATTEMPT_FAILED` | ✅ Preserved |
| Ambiguous results | `06a48ae8` | `AMBIGUOUS_RESULTS_APPROVED` (`224c4285…`) | ✅ Recorded |

---

## CI pipeline

`.github/workflows/ci.yml` — triggers on push to `main` or `feature/assessment-improvements`
and on PR to `main`:

| Job | Runs | Passes |
|---|---|---|
| `Python 3.12 Tests` | 476 tests (full suite) | ✅ |
| `Java 21 Maven Tests` | 8 tests | ✅ |

No Ollama, no external services, no production credentials required in CI.

---

## Fresh-clone verification

| Check | Result |
|---|---|
| `git clone` from GitHub | ✅ Exit 0 |
| Python 476 tests | ✅ `476 passed in 4.87s` |
| Java 8 tests | ✅ `BUILD SUCCESS in 3.663s` |
| Evidence directories present | ✅ All three scenario dirs cloned |
| No production files modified | ✅ Confirmed by pre/post-run SHA-256 inventory |
| No secrets in repository | ✅ Scan found only intentional test fixture patterns |

---

## What is NOT included

- ❌ No release, merge to `main`, or deployment
- ❌ No reset of historical workflow attempt budgets
- ❌ No brownfield re-execution (Option A accepted: offline evidence with gaps)
- ❌ No fixed 24-hour TTL (clarification selected "Custom expiration timestamp")
- ❌ No production credentials or Ollama API keys in the repository
