# Charles Schwab Agentic SDLC Assessment

This repository demonstrates a fully governed, reproducible, agent-assisted software delivery lifecycle (SDLC) around a Spring Boot URL shortener. The Python orchestrator analyzes requirements and code, generates architectural designs and implementation plans, enforces human approval interrupts, executes bounded changes in isolated worktrees, generates additive verification tests, and coordinates automated recovery or repair when validation fails.

The project is **approval-gated by design**: AI models cannot directly execute arbitrary shell commands, cannot modify unapproved files, and cannot bypass human review gates or cryptographic audit tracking.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Project Layout](#project-layout)
3. [Environment Setup](#environment-setup)
4. [Maven Dependency & Cache Preparation](#maven-dependency--cache-preparation)
5. [Ollama Model Configuration & Fallback](#ollama-model-configuration--fallback)
6. [Pre-Execution Startup Validation](#pre-execution-startup-validation)
7. [Running the System](#running-the-system)
   - [Mode A: Offline Fixture Demonstrations (Zero Ollama Required)](#mode-a-offline-fixture-demonstrations)
   - [Mode B: Mocked Synthetic Review Workflows](#mode-b-mocked-synthetic-review-workflows)
    - [Mode C: Live-Model Integrated SDLC](#mode-c-live-model-integrated-sdlc)
8. [Human Approval & Governance Gates](#human-approval--governance-gates)
9. [Automated Testing & Acceptance Suite](#automated-testing--acceptance-suite)
10. [Audit Chain Verification](#audit-chain-verification)
11. [Troubleshooting Guide](#troubleshooting-guide)
12. [Execution Security & Known Limitations](#execution-security--known-limitations)

---

## Prerequisites

Before running the orchestrator or application, ensure the following tools are installed:

| Tool | Version Requirement | Purpose | Verification Command |
|---|---|---|---|
| **Python** | `3.12+` (tested with 3.12.15) | LangGraph orchestration & governance | `python3 --version` |
| **Java** | `21 LTS` (Temurin or OpenJDK) | Spring Boot 4.0.8 application runtime | `java -version` |
| **Maven Wrapper** | Included (`./mvnw`) | Hermetic build & test execution | `cd url-shortener && ./mvnw -v` |
| **Git** | `2.30+` | Detached worktree isolation & checkpoints | `git --version` |
| **Ollama** | Optional for offline; required for live runs | Local LLM inference (`qwen2.5-coder:latest`) | `ollama --version` |

> [!IMPORTANT]
> **Ollama is NOT required** for running offline fixture demonstrations (`--demo ...`), running the full 569-test Python regression test suite (`make test-python`), or running the 15-test acceptance suite (`make test-acceptance`). Reviewers can fully evaluate and verify the system in an offline, air-gapped environment.


---

## Project Layout

```text
├── agent-orchestrator/          # Python 3.12 LangGraph orchestrator & governance modules
│   ├── agents/                  # Codebase, design, implementation, test, & validation agents
│   ├── governance/              # Policy engine, plan lineage, feature adapters, & audit logger
│   ├── llm/                     # Ollama provider, Mock provider, resilient fallback provider
│   ├── requirements.txt         # Pinned Python dependencies
│   └── main.py                  # CLI orchestrator & demonstration entrypoint
├── url-shortener/               # Java 21 / Spring Boot 4.0.8 URL shortener application
│   ├── pom.xml                  # Maven configuration & Spring Boot starters
│   ├── mvnw / mvnw.cmd          # Executable Maven Wrapper
│   └── src/                     # Application source and JUnit Jupiter integration tests
├── .agent-workspaces/           # Runtime isolated Git worktrees (never pollutes main tree)
├── docs/                        # Architecture guides, schema definitions, and audit reports
│   └── evidence/                # Historical evidence, cryptographic receipts, and fixture replays
├── Makefile                     # Standard developer commands for setup, tests, and demos
└── .python-version              # Python 3.12 pin for pyenv / mise / asdf
```

---

## Environment Setup

### 1. Automated Setup via Makefile

The recommended setup creates a Python 3.12 virtual environment, installs pinned dependencies, and configures file permissions:

```bash
make setup
```

### 2. Manual Setup

If you prefer executing the steps manually:

```bash
# 1. Create and activate a Python 3.12 virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 2. Upgrade pip and install pinned orchestrator requirements
python -m pip install --upgrade pip
python -m pip install -r agent-orchestrator/requirements.txt

# 3. Ensure Maven Wrapper is executable
chmod +x url-shortener/mvnw
```

### 3. Verify Environment Readiness

Run the automated pre-flight validation check to confirm Python, Java, and Maven paths:

```bash
make validate-env
```

---

## Maven Dependency & Cache Preparation

The Java application and isolated worktrees require Maven dependencies (Spring Boot, Hibernate, H2, JUnit Jupiter).

### Prepare Offline Cache
To download all dependencies into your local Maven cache for offline execution:

```bash
make cache-maven
# Or directly:
cd url-shortener && ./mvnw dependency:go-offline
```

### Supplying an Explicit Maven Cache
When running full SDLC or parallel multi-task workflows, pass the cache directory:

```bash
python agent-orchestrator/main.py --maven-cache ~/.m2/repository
```

---

## Ollama Model Configuration & Fallback

Ollama is only required when executing live generative workflows (`LLM_PROVIDER=ollama`).

### 1. Start Ollama and Pull Primary Model

```bash
# Start Ollama service (in a separate terminal or background daemon)
ollama serve

# Pull the default primary model (Qwen 2.5 Coder 7B)
ollama pull qwen2.5-coder:latest
```

### 2. Fallback Model Setup

The orchestrator includes a `ResilientModelProvider` that retries transient failures and falls back to a secondary model if configured:

```bash
# Pull optional secondary model
ollama pull llama3.2:3b

# Configure environment variables in .env or shell:
export OLLAMA_HOST=http://localhost:11434
export OLLAMA_MODEL=qwen2.5-coder:latest
export OLLAMA_FALLBACK_MODEL=llama3.2:3b
export MODEL_MAX_RETRIES=2
```

---

## Pre-Execution Startup Validation

To prevent late-stage execution failures deep inside workflows, `main.py` enforces startup validation:

1. **Python Version Check**: Enforces Python `>= 3.12`. Rejects older runtimes with actionable instructions.
2. **Java Version Check**: Confirms `java` is found on PATH and is major version `21`.
3. **Workspace & Maven Integrity**: Confirms `url-shortener/pom.xml` exists, `mvnw` is present, and `mvnw` is executable (`chmod +x`).
4. **Live Model Availability**: When running live (`LLM_PROVIDER=ollama`), checks that the Ollama HTTP server is reachable and `qwen2.5-coder:latest` is installed. Bypassed for offline demos and mock tests.
5. **Feature Adapter Validation**: Confirms the obligation contract or requirement resolves to an approved feature adapter in the registry (`expiration` or `health-check`).

---

## Running the System

### Mode A: Offline Fixture Demonstrations

The repository provides three offline fixture replay demonstrations. These replay verified assessment evidence, exercise isolated worktrees, run real Java Maven validation, and record hash-chained audit events **without requiring Ollama**:

#### 1. Greenfield Scenario — URL Health Component
Demonstrates sequential single-task addition of `UrlHealthComponent` and `UrlHealthComponentTest` in an isolated pre-promotion workspace with real Maven test passing (9 tests):

```bash
make demo-greenfield
# Equivalent: .venv/bin/python agent-orchestrator/main.py --demo greenfield
```

#### 2. Brownfield Scenario — Read-Only URL Status API
Demonstrates multi-file brownfield implementation (`UrlService.java`, `UrlController.java`, and `UrlStatusIntegrationTest.java`) on an isolated baseline with 11 Maven tests passing:

```bash
make demo-brownfield
# Equivalent: .venv/bin/python agent-orchestrator/main.py --demo brownfield
```

#### 3. Ambiguous Requirement Scenario — Clarification Gate
Demonstrates interactive requirement clarification for an ambiguous custom expiration prompt without default TTL assumptions:

```bash
make demo-ambiguous
# Equivalent: .venv/bin/python agent-orchestrator/main.py --demo ambiguous
```

#### 4. Run All Demos

```bash
make demo-offline
```

---

### Mode B: Mocked Synthetic Review Workflows

Run the orchestrator CLI with deterministic synthetic review responses:

```bash
make demo-mock
# Equivalent:
cd agent-orchestrator
LLM_PROVIDER=mock MOCK_SCENARIO=url-expiration ../.venv/bin/python main.py
```

---

### Mode C: Live-Model Integrated SDLC

Execute a live end-to-end SDLC lifecycle using local Ollama:

```bash
# Ensure Ollama is running and qwen2.5-coder:latest is installed
ollama serve

# Execute the complete integrated SDLC lifecycle
.venv/bin/python agent-orchestrator/main.py --full-sdlc
```

With explicit machine-checkable requirement contract and offline cache:

```bash
.venv/bin/python agent-orchestrator/main.py \
  --full-sdlc \
  --design \
  --plan-lineage \
  --contract-file docs/evidence/assessment-scenarios/contracts/custom-expiration.json \
  --maven-cache ~/.m2/repository
```

---

## Human Approval & Governance Gates

The orchestrator enforces human review at every critical transition. When the CLI prompts for a decision, enter the corresponding number:

| Gate | Payload Type | Description | Available Choices |
|---|---|---|---|
| **Clarification** | `REQUIREMENT_CLARIFICATION` | Interactive prompt for ambiguous requirements | Answer numbered options or enter text; confirm (`1` approve / `2` reject) |
| **Obligation Contract** | `REQUIREMENT_CONTRACT_APPROVAL` | Review machine-checkable obligation list | `1` = Approve obligations, `2` = Reject |
| **Architecture Design** | `DESIGN_APPROVAL` | Structured architecture and API contracts | `1` = Approve, `2` = Reject, `3` = Request corrections |
| **Plan Approval** | `PLAN_APPROVAL` | Review dependency-aware task DAG | `1` = Approve, `2` = Reject, `3` = Request requirement changes |
| **Recovery Plan** | `RECOVERY_PLAN_APPROVAL` | Revised plan after validation failure | `1` = Approve recovery plan, `2` = Reject |
| **Repair Approval** | `REPAIR_APPROVAL` | Single exact file patch proposal | `1` = Approve edit, `2` = Reject |
| **Final Release Review** | `FINAL_HUMAN_REVIEW` | Review release evidence, facts, & disclaimer | `1` = Authorize production release, `2` = Reject |

> [!NOTE]
> Entering any unexpected input automatically fails closed and rejects the transition.

> [!IMPORTANT]
> **Deterministic Test Decisions in Automated Testing**:
> While live CLI executions require an interactive operator to approve transitions, automated test suites (`make test-python`, `make test-acceptance`) and CI exercise these exact gates programmatically using deterministic `Command(resume=...)` payloads to verify interrupt handling, checkpointing, and fail-closed security without claiming live human approval. In all automated runs, production release authorization evaluates strictly to `deployment_authorized = False` (`BLOCKED`).

---

## Automated Testing & Acceptance Suite

### 1. Python Orchestrator Regression Suite (569 tests)

Runs all deterministic unit and lifecycle tests, mocking model outputs and testing rollbacks, policy checks, recovery, and plan lineage:

```bash
make test-python
# Equivalent:
cd agent-orchestrator && LLM_PROVIDER=mock MOCK_SCENARIO=url-expiration ../.venv/bin/python -m pytest . -q
```

### 2. Java Spring Boot Test Suite (32 tests)

Runs all unit, database collision, repository, controller, and status integration tests:

```bash
make test-java
# Equivalent:
cd url-shortener && ./mvnw clean test
```

### 3. End-to-End Acceptance Suite (15 tests)

Runs focused end-to-end acceptance tests verifying genuine Maven execution, parallel downstream SDLC stages, generic non-expiration feature execution, deterministic human approval gates, and failure rollbacks:

```bash
make test-acceptance
# Equivalent:
cd agent-orchestrator && \
LLM_PROVIDER=mock MOCK_SCENARIO=url-expiration \
../.venv/bin/python -m pytest \
  test_generated_expiration_regression.py \
  test_parallel_sdlc_lifecycle.py \
  test_generic_sdlc_lifecycle.py \
  test_final_human_review.py \
  test_rollback.py \
  test_repair_approval.py \
  -v
```

### 4. Run Both Regression Suites

```bash
make test
```

---

## Audit Chain Verification

Every workflow event (approvals, file checkpoints, transaction journals, Maven exit codes, and test digests) is cryptographically signed and hash-chained into SQLite:

Verify ledger integrity:

```bash
make verify-audit
# Equivalent:
.venv/bin/python agent-orchestrator/main.py --verify-audit agent-orchestrator/audit.db
```

Output:
```text
AUDIT CHAIN INTEGRITY: VALID (records=..., head_id=...)
```

---

## Troubleshooting Guide

### 1. Java Version Mismatch or Missing
* **Symptom**: `STARTUP VALIDATION FAILED: Unsupported Java version: detected Java 17 (expected Java 21)` or `Java runtime not found on PATH`.
* **Remediation**:
  - Install Java 21 (e.g. Eclipse Temurin 21 via `brew install --cask temurin@21` on macOS, or `apt install openjdk-21-jdk` on Linux).
  - Configure `JAVA_HOME` and PATH:
    ```bash
    export JAVA_HOME=$(/usr/libexec/java_home -v 21) # macOS
    export PATH="$JAVA_HOME/bin:$PATH"
    ```

### 2. Ollama Connection Refused
* **Symptom**: `STARTUP VALIDATION FAILED: Ollama service is not reachable at 'http://localhost:11434': <urlopen error [Errno 61] Connection refused>`.
* **Remediation**:
  - Start the Ollama server:
    ```bash
    ollama serve
    ```
  - If you only want to review the system offline without Ollama:
    ```bash
    make demo-greenfield   # or demo-brownfield / demo-ambiguous / demo-offline
    # Or run the test suite:
    make test
    ```

### 3. Model Not Pulled in Ollama
* **Symptom**: `STARTUP VALIDATION FAILED: Configured primary model 'qwen2.5-coder:latest' is not installed in local Ollama`.
* **Remediation**:
  - Download the model:
    ```bash
    ollama pull qwen2.5-coder:latest
    ```

### 4. Maven Wrapper Permission Denied
* **Symptom**: `STARTUP VALIDATION FAILED: Maven wrapper '.../url-shortener/mvnw' is not marked executable`.
* **Remediation**:
  - Grant execution permissions:
    ```bash
    chmod +x url-shortener/mvnw
    ```

### 5. Maven Dependencies Failing in Air-Gapped / Offline Environments
* **Symptom**: Maven attempts network downloads and fails when internet access is unavailable.
* **Remediation**:
  - Pre-populate dependencies while online using `make cache-maven`.
  - Pass the offline directory via `--maven-cache <directory>`.

### 6. Unsupported Feature Adapter
* **Symptom**: `STARTUP VALIDATION FAILED: No approved feature adapter found for requested feature`.
* **Remediation**:
  - Supported feature adapters are `expiration` (URL expiration with Clock boundary tests) and `health-check` (URL Health Component).
  - Use an approved obligation contract JSON (such as `docs/evidence/assessment-scenarios/contracts/custom-expiration.json`).

---

## Execution Security & Known Limitations

### Execution Security Controls
AI-generated code and build validation execute within an enforced security boundary managed by `governance/execution_security.py` and `governance/source_policy.py`:
- **Subprocess Environment Sanitization**: Strips inherited credentials, secrets, API tokens (`AWS_*`, `GITHUB_*`, `DATABASE_*`, `OPENAI_*`, `ANTHROPIC_*`, `OLLAMA_*`, etc.), and JVM override flags (`MAVEN_ARGS`, `MAVEN_OPTS`, `JAVA_TOOL_OPTIONS`, `JDK_JAVA_OPTIONS`) before launching child processes.
- **Strict Workspace Containment**: Confirms all modified and executed files resolve within `.agent-workspaces/`, blocking directory traversal (`..`), host absolute path escapes (e.g. `/etc/passwd`), and directory symlink escapes.
- **Task-Scoped File Allowlists**: Enforces that only authorized source files reviewed in the task plan can be edited.
- **Offline Maven Execution**: Downstream stages pass `./mvnw -o` with a pinned local dependency cache (`-Dmaven.repo.local`) to prevent unverified outbound network dependency downloads.
- **Bounded Timeouts**: Subprocess execution timeouts are strictly capped (`<= 180s`) to avoid hung processes.

### Explicit Security Disclosures & Limitations
- **OS-Level Sandboxing is Not Implemented**: The system does not employ kernel sandboxing (e.g., Linux seccomp, AppArmor, macOS sandbox-exec) or container runtimes (Docker/Podman). Rigid OS-level sandboxing was previously documented to prevent Java 21 Byte Buddy / Mockito self-attachment during Spring Boot integration tests (`Byte Buddy could not self-attach to current VM`).
- **In-JVM Host Access Risk**: Because OS process boundaries are not virtualized, arbitrary Java test code running inside the JVM runs under the host user's process privileges. Hallucinated or malicious Java test code could read files accessible to the executing user or make network calls if host network egress is enabled. Production enterprise deployment requires running builds inside dedicated virtualized containers or isolated VMs.

### Additional Known Limitations
- **Model Execution Context**: Live workflows require local Ollama with sufficient VRAM/RAM to host `qwen2.5-coder:latest` (approximately 5 GB).
- **In-Memory Database**: The Spring Boot demonstration uses H2 in-memory storage; production persistence (e.g. PostgreSQL) is outside the assessment scope.
- **Allowed Source Scope**: Generic implementation agents are constrained by the policy engine to existing files under `url-shortener/src/main/java` and `url-shortener/src/test/java`.
- **Additive Test Renderer**: Generated test cases are compiled via vetted feature adapters to guarantee test integrity and prevent arbitrary disabling of baseline assertions.
