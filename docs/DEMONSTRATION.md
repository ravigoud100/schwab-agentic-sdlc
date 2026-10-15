# Assessment Demonstration Guide

This guide is designed for a technical evaluator. It uses the implementation currently present in the repository and separates live AI behavior from deterministic mocked tests.

## Preparation

1. Verify Java 21:

   ```bash
   java -version
   ```

2. Verify Python 3.12 and create the environment described in the root [README.md](../README.md).
3. Start Ollama and make `qwen2.5-coder:latest` available.
4. Run the Java baseline:

   ```bash
   cd url-shortener
   ./mvnw clean test
   ```

5. Keep the completed Java application untouched while demonstrating the orchestrator. The implementation and validation agents use `.agent-workspaces/url-expiration/`.

## Demonstration 1: Requirement analysis and planning

Run the orchestrator:

```bash
cd agent-orchestrator
../.venv/bin/python main.py
```

Enter a requirement such as:

```text
Add URL expiration support while preserving active redirects, returning HTTP 410 for expired URLs, and preventing click-count increments for expired URLs.
```

Show:

- Requirements Agent output and its explicit acceptance/risk analysis.
- Codebase Agent output based on the actual Java source tree.
- Planning Agent output, including affected files, tests, dependencies, and approval requirements.
- The generated task state. Strict JSON task plans become DAG tasks; prose plans use the safe single-task compatibility fallback.

## Demonstration 2: Human plan approval

At the plan interrupt:

- Enter `1` to approve and continue to task scheduling.
- Enter `2` or any unexpected value to reject and safe-stop.

The approval is written to SQLite as `PLAN_APPROVED` or `PLAN_REJECTED`. A rejected plan must not call the implementation agent.

To inspect events without exposing source or model output:

```bash
sqlite3 agent-orchestrator/audit.db \
  'select workflow_id, timestamp, agent, event from audit_events order by id;'
```

## Demonstration 3: Task dependencies

Use a structured plan response containing tasks such as:

```json
{
  "tasks": [
    {
      "task_id": "entity",
      "description": "Update the URL entity",
      "dependencies": [],
      "allowed_files": ["url-shortener/src/main/java/url_shortener/ShortUrl.java"]
    },
    {
      "task_id": "validation",
      "description": "Run validation",
      "dependencies": ["entity"],
      "allowed_files": ["url-shortener/src/test/java/url_shortener/UrlExpirationIntegrationTests.java"]
    }
  ]
}
```

The scheduler validates duplicate IDs, missing dependencies, cycles, path traversal, and task states. It reports independent ready batches when file sets do not overlap. Current execution remains deterministic and selects one eligible task at a time; it does not start concurrent workers.

The deterministic tests for these behaviors are in [test_task_orchestration.py](../agent-orchestrator/test_task_orchestration.py).

## Demonstration 4: Implementation and Maven validation

After approval, show the following sequence in the console and audit database:

1. `TASKS_SCHEDULED` and `TASKS_STARTED`.
2. `IMPLEMENTATION_COMPLETED`.
3. `VALIDATION_PASSED` or `VALIDATION_FAILED`.
4. `TASKS_COMPLETED` when the task passes.
5. `AWAITING_FINAL_HUMAN_REVIEW` after all tasks pass.

The real validation command is `./mvnw clean test` in the isolated URL-expiration worktree. The validator records pass/fail, exit code, duration, and bounded output in LangGraph state; the audit database receives metadata only.

## Demonstration 5: Repair and replanning

To demonstrate the failure path, use a deterministic test or a controlled validation failure rather than intentionally damaging the completed Java application.

The expected sequence is:

1. Validation fails.
2. The Replanning Agent receives requirement, current plan, failure details, and retry history.
3. A revised plan and change summary receive a new version.
4. The workflow pauses at `REPLAN_APPROVAL`.
5. Rejection, invalid output, or an unchanged plan stops safely.
6. Approval proceeds to repair proposal and a separate `REPAIR_APPROVAL` interrupt.
7. Only a structured allowlisted exact replacement can execute.
8. Maven validation runs again.
9. Failed repair validation triggers hash-checked rollback and an audit event.

Relevant deterministic tests:

- [test_replanning.py](../agent-orchestrator/test_replanning.py)
- [test_repair_execution.py](../agent-orchestrator/test_repair_execution.py)
- [test_workflow_scenarios.py](../agent-orchestrator/test_workflow_scenarios.py)

These tests mock model and Maven results. They verify governance and routing; they do not claim autonomous live AI success.

## Demonstration 6: Audit and checkpoint evidence

Use the workflow ID printed at the end of the run to filter audit records:

```bash
sqlite3 agent-orchestrator/audit.db \
  "select timestamp, agent, event, details from audit_events where workflow_id = '<WORKFLOW_ID>' order by id;"
```

Expected categories include:

- Human decisions: plan, replan, and repair approval/rejection.
- Task lifecycle: scheduled, started, completed, failed, or blocked.
- Validation: pass/fail metadata.
- Repair: proposed, executed, blocked, rolled back, or stopped.

LangGraph checkpoint state is stored separately in `agent-orchestrator/workflow.db` and supports resuming an interrupted approval by thread ID.

## Evaluator caveats

- Do not treat mocked Python tests as proof of live model quality.
- Do not expect the orchestrator to execute shell commands returned by a model; it explicitly does not.
- Do not expect true parallel workers; independent batches are detected and reported, but execution is sequential.
- Do not remove or bypass human approval, Maven validation, retry limits, or rollback checks during the demonstration.
- Do not place secrets or personal information in prompts, audit records, or documentation.
