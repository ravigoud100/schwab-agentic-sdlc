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

1. The Requirements Agent analyzes the requested change.
2. The Codebase Agent reads the actual Java source tree and reports observed architecture and endpoints.
3. The Planning Agent creates a plan. JSON task plans are converted into dependency-aware tasks; non-JSON legacy plans use a safe single-task fallback.
4. A human approves or rejects the plan.
5. The task scheduler validates the DAG, identifies ready tasks, and starts one task deterministically.
6. The Implementation Agent operates only on the approved isolated Java file and skips generation when the required expiration implementation already exists.
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
