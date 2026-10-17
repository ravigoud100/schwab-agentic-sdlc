# Charles Schwab Agentic SDLC Assessment

This repository demonstrates a governed, agent-assisted software delivery workflow around a Spring Boot URL shortener. The Python orchestrator analyzes requirements and code, creates an implementation plan, pauses for human approval, performs controlled implementation and validation, and can propose approved repairs when validation fails.

The project is intentionally approval-gated. AI output does not directly execute shell commands, and source changes are restricted to the isolated workflow worktree.

## Project layout

```text
url-shortener/                 Spring Boot 4 URL shortener
agent-orchestrator/            LangGraph workflow and governance modules
.agent-workspaces/<workflow_id>/
                               Isolated validation/repair worktree
docs/                          Architecture and demonstration guides
```

The main Java source is under `url-shortener/`. The implementation and validation agents target the isolated `.agent-workspaces/<workflow_id>/` worktree so experiments and rollback checkpoints do not overwrite the completed application.

## Objectives

- Demonstrate requirement analysis, codebase analysis, planning, approval, implementation, and validation.
- Make repair execution explicit, bounded, auditable, and rollback-safe.
- Replan after validation failures using failure output, the current plan, and retry history.
- Schedule approved implementation tasks using dependency validation and deterministic ordering.
- Preserve Java 21 and Spring Boot 4 compatibility.

## Architecture and technologies

| Area | Technology or implementation |
| --- | --- |
| Application | Java 21, Spring Boot 4.0.8, Spring Web MVC, Spring Data JPA, H2 |
| Application tests | Spring Boot integration tests with MockMvc and JUnit Jupiter through Spring Boot test starters |
| Orchestration | Python 3.12, LangGraph, LangChain Ollama integration |
| Model runtime | Ollama with `qwen2.5-coder:latest` |
| Persistence | H2 for the URL shortener; SQLite for LangGraph checkpoints and audit events |
| Build | Maven Wrapper (`mvnw`) |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the detailed workflow and governance design.

## Prerequisites

- Java 21.
- Python 3.12.
- Maven, or the included Maven Wrapper.
- Ollama running locally with `qwen2.5-coder:latest` available.
- Python packages used by the orchestrator: `langgraph`, `langgraph-checkpoint-sqlite`, `langchain-ollama`, `python-dotenv`, and `pytest` for tests.

There is currently no committed Python dependency lockfile or `requirements.txt`; install the packages in a project virtual environment and record any organization-specific pinning outside the source tree.

## Setup

Create and activate a Python environment from the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install langgraph langgraph-checkpoint-sqlite langchain-ollama python-dotenv pytest
```

Start Ollama and download the configured model:

```bash
ollama serve
ollama pull qwen2.5-coder:latest
```

The orchestrator loads environment variables with `python-dotenv`, but the checked-in code does not require a secret or API key for its Ollama configuration.

## Run the Java URL shortener

From the Java project directory:

```bash
cd url-shortener
./mvnw clean test
./mvnw spring-boot:run
```

The service listens on the Spring Boot default port, `8080`.

Create a URL without expiration:

```bash
curl -X POST http://localhost:8080/api/urls \
  -H 'Content-Type: application/json' \
  -d '{"originalUrl":"https://www.google.com"}'
```

Create one with a future expiration:

```bash
curl -X POST http://localhost:8080/api/urls \
  -H 'Content-Type: application/json' \
  -d '{"originalUrl":"https://www.google.com","expiresAt":"2099-01-01T00:00:00Z"}'
```

The application exposes URL creation under `/api/urls`, redirects active short codes from `/{shortCode}`, and exposes click analytics at `/api/urls/{shortCode}/analytics`. Past expiration returns HTTP 400 during creation; expired redirects return HTTP 410; unknown short codes return HTTP 404.

## Run the Python orchestrator

From the orchestrator directory:

```bash
cd agent-orchestrator
../.venv/bin/python main.py
```

Enter a requirement when prompted. The workflow pauses for plan approval and, when needed, revised-plan and repair approvals. The orchestrator creates `workflow.db` for LangGraph checkpoints and `audit.db` for audit events in `agent-orchestrator/`.

The live workflow requires a reachable Ollama service. AI-generated plans and repair proposals are not a substitute for human approval.

## Automated tests

Run Java integration tests:

```bash
cd url-shortener
./mvnw clean test
```

The current Java suite covers application startup plus URL creation with and without expiration, future/past expiration, active redirects, expired redirects, unknown codes, and click-count protection for expired URLs.

Run deterministic orchestrator tests from `agent-orchestrator/`:

```bash
../.venv/bin/python -m pytest -v \
  test_repair_execution.py \
  test_replanning.py \
  test_task_orchestration.py \
  test_workflow_scenarios.py
```

These tests mock model responses, Maven validation, file changes, and rollback where appropriate. They do not prove live Ollama behavior. Other `test_*.py` files include direct scripts or model-dependent checks and should be reviewed before running as an indiscriminate suite.

## Example workflow

1. The Requirements Agent returns validated JSON; unresolved ambiguities pause for human answers before codebase analysis.
2. The Codebase Agent reads the actual Java source tree and reports observed architecture and endpoints.
3. The Planning Agent creates a plan. JSON task plans are converted into dependency-aware tasks; non-JSON legacy plans use a safe single-task fallback.
4. A human approves or rejects the plan, or requests requirement changes that invalidate it.
5. The task scheduler validates the DAG, identifies ready tasks, and starts one task deterministically.
6. The generic Implementation Agent proposes coordinated edits only to approved existing Java files in the isolated workspace. Validation-only tasks bypass source generation.
7. The Validation Agent runs `./mvnw clean test` in the isolated URL-shortener worktree.
8. A passing validation completes eligible tasks and reaches final human review.
9. A failure triggers versioned replanning. A human must approve a meaningful revised plan before repair analysis and repair approval can continue.
10. An approved structured repair performs one exact replacement in the allowlisted Java file, validates again, and rolls back safely if validation fails.

## Human approval and governance controls

- Plan approval is required before scheduling or implementation.
- Revised plans require a separate human approval interrupt.
- Repair proposals must be strict structured data and must pass the repair allowlist before execution.
- Repair execution rejects absolute paths, `..` traversal, symlink escapes, unsupported fields, and non-unique expected text.
- A rollback checkpoint is created before an exact repair replacement.
- Validation runs through the Maven Wrapper with a timeout and records metadata rather than full Maven output in SQLite audit events.
- Retry behavior is bounded by `MAX_RETRIES = 2`.
- Audit events include plan decisions, replanning, task scheduling, implementation, validation, repair, and rollback outcomes.
- LangGraph state is checkpointed with SQLite so interrupted human approvals can resume by workflow ID.

## Known limitations

- Ollama is required for live requirement, codebase, planning, implementation, repair, and replanning agents.
- Python dependencies are not currently pinned in a committed requirements or lock file.
- The task scheduler validates dependencies and reports safe independent batches, but the current implementation executes one eligible task at a time; it does not launch concurrent task workers.
- The legacy plan fallback creates one implementation task when the planning response is not strict task JSON.
- The repair agent proposes changes; only the structured, allowlisted exact replacement is executable after human approval.
- The orchestrator's deterministic tests use mocks and do not replace live Ollama, Maven, or end-to-end environment validation.
- The application uses the default in-memory H2 configuration shown in the repository; production persistence, authentication, and deployment infrastructure are outside this assessment.

Each CLI run creates a detached Git worktree at `.agent-workspaces/<workflow_id>/` from committed `HEAD`. Commit any source changes you want included before starting a run. The resolved workspace path is persisted in workflow state and passed explicitly to analysis, implementation, validation, repair, checkpoint creation, and rollback. Worktrees are retained for review; an existing workspace is never overwritten during initialization.

LLM calls pass an explicit `stage`: `requirements`, `codebase`, `planning`, `implementation`, or `repair` (replanning also uses `planning`). Ollama ignores this routing metadata. For predefined synthetic review responses, set `LLM_PROVIDER=mock` and `MOCK_SCENARIO=url-expiration`. Mock routing uses the selected scenario and stage only, never prompt keywords. Unknown scenarios and stages without vetted responses raise before generation can write source. The current mock scenario supports requirements, codebase, and a structured `verify-expiration` plan. After approval, that task follows a validation-only route: it runs Maven validation without source generation or rollback checkpoints. A failed verification stops without replanning or repair. Implementation and repair responses remain unavailable. This scenario verifies existing functionality; it does not demonstrate new implementation. Maven still requires a local Java installation and available build dependencies for offline execution. Existing model patch points and legacy response normalization in replanning remain available during migration.

Implementation tasks now execute through `implement_task(task, workspace, context)`. Approval is bound to the task definitions; the executor uses the requirement, analysis, plan, task description, and current file contents to request an exact JSON replacement (`path`, `old_text`, `new_text`). It rejects unauthorized paths, malformed output, ambiguous replacements, and concurrent source changes before writing. The generic executor supports coordinated edits across existing Java source files under `url-shortener/src/main/java` or `url-shortener/src/test/java`. New files remain unsupported. The workflow checkpoints the approved file and passes its allowlist through hash-checked rollback. Unsupported mock implementations stop at `STOPPED_IMPLEMENTATION_BLOCKED` before Maven runs. The validation-only scenario continues to bypass generation. The legacy `implement_expiration` entry point remains for standalone demonstration compatibility; the workflow no longer calls it.

For a live single-file implementation acceptance run, use `python scripts/live_implementation_demo.py prepare /absolute/path/to/new-demo` from `agent-orchestrator/`. It creates a temporary Git snapshot with independent click-limit acceptance tests, proves the method is absent with a failing Maven baseline, then runs real Ollama analysis and planning. It persists the approval interrupt in SQLite and writes `plan.json` for human review. Resume only after reviewing that plan: `python scripts/live_implementation_demo.py resume /absolute/path/to/new-demo --decision approve` (or `reject`). A fresh prepared run can use `--force-failure` on resume to deliberately fail a preinstalled Maven test and verify rollback. Generic implementation failures now roll back immediately rather than entering the legacy expiration repair path. The runner uses an offline copy of the existing Maven cache; a working local Ollama server and cached Java dependencies are required. The snapshot, baseline, source evidence, audit database, LangGraph checkpoints, and result remain in the chosen directory. This records the earlier bounded single-file scenario; subsequent multi-file and clarification milestones are described below. Expanded SDLC orchestration remains unfinished.

Live single-file acceptance evidence is recorded in [docs/evidence/live-click-limit/README.md](docs/evidence/live-click-limit/README.md), including a successful real-AI implementation, genuine Maven validation, and a separate deliberate-failure rollback run. Both were explicitly approved by the user.


Multi-file implementation uses `{"edits": [{"path": "...", "old_text": "...", "new_text": "..."}]}` with edits for every file in the human-approved task allowlist; multiple non-overlapping edits to a file are supported. Every proposal is checked before staging. A workspace lock serializes cooperating executors, checkpoints and original hashes are recorded before any source replacement, and per-file atomic replacements are compensated as a set on application or Maven failure. SQLite audit events identify every checkpoint and transaction. Durable recovery journals are retained in the isolated workspace's `.agent-transactions/`; unfinished operations block new implementations until restored. `finish_transaction(transaction_id, workspace, approved_files, passed=False)` preflights every checkpoint and current hash before restoring the set. Unexpected external modifications stop recovery for review instead of being overwritten. These are recoverable multi-file transactions, not simultaneous filesystem visibility or protection against arbitrary external writers/storage failure.

The coordinated API demo is prepared with `python scripts/live_multifile_demo.py prepare /absolute/path/to/new-demo`. It installs independent status-endpoint acceptance tests before AI execution, proves the feature is absent with real Maven, and pauses at the generated two-file plan. Resume with an explicit approved decision using the same script and `resume DIRECTORY --decision approve`. A fresh fork (`fork NEW_DIRECTORY --source ORIGINAL_DIRECTORY`) reuses that exact live-generated plan and baseline for a separate `resume NEW_DIRECTORY --decision approve --force-failure` rollback test. Source edits require approval of the new controller/service plan.

The [live two-file status API acceptance report](docs/evidence/live-status-api/README.md) records real model proposals, 13-test Maven success, deliberate-failure restoration of both files, per-file checkpoint audit events, and all unsuccessful attempts. The report also documents model reliability and transaction limits.

Generic implementation failures now enter an automatic recovery path by default. It collects structured failure evidence and restores the transaction before asking the model for a targeted recovery plan. Task-definition changes require fresh human approval; unchanged task definitions retain their existing approval. `MAX_RETRIES=2` limits execution to three attempts including the first. Infrastructure failures, authorization violations, unexpected source hashes, invalid recovery plans, and failed rollback stop safely. Validation-only tasks retain their read-only stop behavior, and the earlier legacy repair path remains available separately.

The [single-workflow recovery evidence](docs/evidence/autonomous-recovery/README.md) records a controlled compiler defect, automatic rollback and diagnostic transfer, live recovery-plan version 2, a live corrected edit, and 13 passing Maven tests on attempt 2. Reproduce using `python scripts/autonomous_recovery_demo.py prepare NEW_DIRECTORY`, then `advance NEW_DIRECTORY --decision approve` after plan review. Material recovery plans pause for another explicit approval; no approval is supplied by the model. The earlier standalone live demonstration scripts explicitly disable autonomous recovery to preserve their historical single-attempt success/forced-rollback behavior.


New workflow runs require a strictly validated JSON requirements contract before codebase analysis. Unresolved ambiguities produce a persisted `REQUIREMENT_CLARIFICATION` interrupt; suggested defaults never count as answers. Every question requires an explicit answer and approval. Invalid or incomplete answers re-interrupt without entering planning. Answers, requirement versions, and hashes are persisted in LangGraph state and SQLite audit events and included in downstream analysis and planning.

At initial engineering plan review, `request_changes` accepts revised requirement text, invalidates downstream plans and approvals, and starts requirements analysis again on the same thread. Changed requirements must resolve their own ambiguities before receiving a new plan. Scheduling and implementation check the plan's requirement version/hash and its approval binding. Direct low-level calls retain compatibility with older state shapes; new workflows entering through START always enforce the strict contract. Schema validation establishes structure, not semantic completeness of model analysis.

The clarification demo uses `python scripts/clarification_demo.py prepare NEW_DIRECTORY`, then `answer NEW_DIRECTORY --answers-file ANSWERS_JSON --decision approve` after human review. The answers file maps ambiguity IDs to exact offered choices (or text for free-text questions). It resumes the same SQLite thread and stops at engineering plan approval without executing source edits. See [requirements clarification evidence](docs/evidence/requirements-clarification/README.md). The focused regression selection now passes 139 tests, including clarification, requirement changes, implementation, recovery, validation, and rollback.

Explicit machine-checkable requirement contracts can now be supplied with `python main.py --contract-file CONTRACT_JSON`. They require their own human review before planning; the expiration acceptance profile is defined in `governance/semantic_plan.py`. Plans must contain structured `coverage` records mapping every approved obligation's exact behavior/test specification to an implementation task and its allowed files. The semantic gate checks declarations without keyword inference or an LLM judge. Inadequate proposals feed validation errors back to the planner, with two regenerations maximum; exhaustion stops before plan approval. Contract approval is bound to requirement version and decision hash, and implementation checks the accepted plan hash. Requirement changes invalidate this binding and require a replacement reviewed contract. Legacy workflows without explicit contracts retain structural-only planning. This gate establishes declared coverage, not implementation correctness or general semantic completeness.

[Semantic planning evidence](docs/evidence/semantic-plan/README.md) retains the failed schema-conflict run and the corrected live run: a deliberately generic proposal was rejected and real Ollama automatically produced a custom UTC expiration plan covering all eight approved obligations. Both branches preserve the clarified requirement identity; neither modified source or ran Maven. The expanded focused regression selection passes 158 tests.

Architecture/design is available as an opt-in staged rollout with `python main.py --design --contract-file CONTRACT_JSON`. After clarification, codebase analysis, and obligation-contract review, the design entry gate requires resolved requirement identity, valid contract approval, an initialized workspace marker, and no unfinished source transaction. The design agent emits structured API contracts and validation rules, components/interactions, data-model changes, compatibility risks/mitigations, architectural decisions/rationales, and exact obligation coverage. Exit checks reject missing/duplicate obligations, unresolved references, unauthorized component paths, incorrect optional UTC input contracts, omitted future-only validation rules, and test obligations mapped to production components. Invalid artifacts receive bounded diagnostic feedback; at most three generation attempts are allowed.

Every valid design requires a separate human design approval before task planning. The approval binds the artifact to the requirement version, decision hash, and reviewed contract hash. Planning receives that artifact, and downstream plan gates require the current approved design hash; requirement changes clear old design and plan approvals. Legacy workflows without `--design` retain their current path. These are design-stage gates and downstream prerequisites, not a claim that every future SDLC stage is implemented. Test generation, documentation, release readiness, and parallel workers remain future milestones.

[Architecture/design evidence](docs/evidence/architecture-design/README.md) records a live artifact passing design gates and pausing for design approval, zero Java changes, the exhausted and superseded branches, and remaining narrative-review issues. The focused regression selection now passes 180 tests, including 22 design-stage tests.

Design review now supports `request_changes` with explicit nonempty feedback. The workflow archives the prior artifact/hash and review decision, clears design/plan approvals, regenerates under the same requirement identity and approved obligation contract, revalidates the gates, and returns to human review. Every new successful artifact receives a design version and version-bound hash; old hashes cannot approve a revision. Each human correction request allows at most three generation attempts, and unchanged artifacts are rejected. Invalid review payloads re-interrupt without generation. See [the controlled design-correction evidence](docs/evidence/design-correction/README.md). The focused selection now passes 189 tests, including nine correction tests.

A separate opt-in test-stage subworkflow now supports the reviewed custom-expiration/Clock scenario. It requires explicit approval of the exact architecture amendment, single production-file patch, and additive test-file path. Independent external snapshots preserve original tests/build files byte-for-byte; model output is constrained case JSON compiled by a trusted renderer, so arbitrary assertion edits, disabling, exclusions, and build changes fail closed. Real Maven gates require original tests to remain executable and verify both a controlled equality-boundary negative fixture and the correct implementation. [Test-generation evidence](docs/evidence/test-generation/README.md) records 8 original tests passing, one meaningful negative assertion failure, and 12 final passing tests. The prior four-file plan remains unapproved. This vetted-renderer subworkflow is not yet automatic main-workflow integration or arbitrary Java test synthesis. The focused regression selection passes 219 tests.
