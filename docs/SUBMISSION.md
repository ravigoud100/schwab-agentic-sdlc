# Agentic SDLC Assessment — Final Submission Report

**Project:** Charles Schwab Agentic SDLC — URL Shortener
**Repository:** https://github.com/ravigoud100/schwab-agentic-sdlc
**Branch:** `main` (merged from `feature/assessment-improvements`)
**Submitted:** 2026-10-18
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

### Python orchestrator (493 tests, Java-free, offline)

```
493 passed in 4.85s
```

- Runtime: Python 3.12, LangGraph 1.2.15, offline mock LLM provider
- Test files: 40 (full suite, including 45 parallel DAG orchestration and Java import diagnostics tests)
- Command: `python -m pytest agent-orchestrator -q`

### Primary Java URL shortener (8 baseline tests)

```
Tests run: 8, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS — 3.663s
```

- Runtime: Java 21.0.12.1 (Temurin), Spring Boot 4.0.8, H2 in-memory DB
- Test classes: `UrlExpirationIntegrationTests` (7) + `UrlShortenerApplicationTests` (1)
- Location: `url-shortener/` (primary source tree)
- Command: `./mvnw --batch-mode clean test` (inside `url-shortener/`)

> [!NOTE]
> **Primary Java Source Tree vs. Approved Brownfield Evidence**
> The primary repository source tree under `url-shortener/` intentionally contains the baseline implementation with **8 passing Maven tests**.
> The successful autonomous brownfield workflow was executed and validated in an isolated workspace (`docs/evidence/assessment-scenarios/brownfield-autonomous-run/workspace/url-shortener`), generating the status endpoint and additive integration tests for **11 passing Maven tests** (8 baseline + 3 status integration). Those generated artifacts remain preserved as approved scenario evidence and have not been promoted into the primary source tree without separate authorization.

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
| Task DAG execution | Dependency-aware multi-wave parallel scheduler with level barriers |
| Checkpoints | SQLite via `langgraph-checkpoint-sqlite` |
| Audit trail | SQLite (`audit.db`) — append-only event log |
| Target service | Java 21, Spring Boot 4.0.8, Maven, H2 (test) |
| Human gates | LangGraph `interrupt()` on plan, test scope, results review |
| Policy engine | 12-policy pre-write checker (SEC, COM, GOV, TEST namespaces) |
| Rollback | File-level checkpoint + multi-wave whole-set transactional revert |

---

## Dependency-aware multi-wave parallel DAG orchestration

The orchestrator includes native multi-wave parallel task execution (`agent-orchestrator/governance/parallel_execution.py`) verified by 45 dedicated automated tests:

- **Topological wave partitioning:** Tasks are partitioned into dependency levels using topological sort. Tasks within each wave have zero interdependencies and strictly disjoint `allowed_files` sets.
- **Concurrent execution with level synchronization:** Independent tasks within a wave execute concurrently (up to configurable `max_concurrency` workers) via LangGraph dynamic fan-out (`Send`), synchronizing at a superstep barrier before the next wave begins.
- **Downstream predecessor consumption:** Dependent tasks in wave $k+1$ execute against the validated, applied source changes of predecessor tasks in waves $0 \dots k$.
- **Whole-set rollback guarantee:** If any task or validation step fails in any wave, all previously validated wave transactions are compensated in reverse order, restoring the repository to its exact pre-orchestration state. If external file tampering occurs before rollback, the system halts with `STOPPED_PARALLEL_RECOVERY_REQUIRED` rather than overwriting external changes.
- **Durable checkpoint resumption:** Completed wave receipts are tracked in state so interrupted workflows resume without re-executing already completed waves.
- **Remaining limitations:** Tasks within the same wave must specify mutually disjoint `allowed_files` sets (no concurrent multi-writer support for a single file); operations are restricted to allowlisted Java production paths.

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
| `Python 3.12 Tests` | 493 tests (full suite) | ✅ |
| `Java 21 Maven Tests` | 8 tests | ✅ |

No Ollama, no external services, no production credentials required in CI.

---

## Fresh-clone verification

| Check | Result |
|---|---|
| `git clone` from GitHub | ✅ Exit 0 |
| Python 493 tests | ✅ `493 passed in 4.85s` |
| Java 8 tests | ✅ `BUILD SUCCESS in 3.663s` |
| Evidence directories present | ✅ All scenario dirs cloned |
| No production files modified | ✅ Confirmed by pre/post-run SHA-256 inventory |
| No secrets in repository | ✅ Scan found only intentional test fixture patterns |

---

## What is NOT included

- ❌ No production release or deployment
- ❌ Primary Java source tree retains the baseline 8-test suite (brownfield 11-test implementation and test scope are preserved as approved isolated evidence in `docs/evidence/assessment-scenarios/brownfield-autonomous-run/`)
- ❌ No reset of historical workflow attempt budgets (`STOPPED_RETRY_EXHAUSTED` preserved)
- ❌ No fixed 24-hour TTL (clarification selected "Custom expiration timestamp")
- ❌ No production credentials or Ollama API keys in the repository
