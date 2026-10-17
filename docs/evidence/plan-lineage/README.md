# Prospective plan-lineage adapter — October 16, 2026

The documentation/readiness continuation has completed with a rejected final review and terminal status `STOPPED_RELEASE_BLOCKED`. [The completion record](../documentation-readiness/blocked-completion.json) and [SQLite final-review audit export](../documentation-readiness/blocked-completion-audit.json) preserve the decision. Documentation, testing, and evidence lineage remain verified. Matching engineering-plan approval and security/operational sign-off remain missing. Deployment authorization is not granted. The original engineering thread remains paused at plan approval; the earlier four-file plan was not approved or executed.

## First integration foundation

The new opt-in adapter records authorization only after explicit engineering-plan approval. Its receipt binds the exact plan, task definitions, requirement version/decision hash, approved contract, approved design, canonical workspace, and project baseline hashes. An independent Clock/test scope cannot substitute for this receipt. Existing source transactions cannot be adopted retrospectively: plan authorization must precede source application and execution binding in the same workflow audit trail.

Before generation, the adapter rechecks approval bindings and the complete protected project inventory. After application, it verifies that the transaction started from the approved baseline and changed exactly the authorized production files. Binding failure invokes the existing compensating rollback. Unexpected external hashes retain the existing safe-stop behavior.

Initial implementation and targeted regeneration now receive authoritative structured requirements, clarification answers, approved obligation contract, approved design, approved plan, and authorization context. This closes the implementation-prompt context gap identified in the earlier engineering-plan review.

Plans using lineage mode declare a top-level test policy:

```json
{
  "test_stage": {
    "allowed_new_test_files": [
      "url-shortener/src/test/java/url_shortener/GeneratedBoundaryTest.java"
    ],
    "protected_existing_test_files": [
      "url-shortener/src/test/java/url_shortener/UrlExpirationIntegrationTests.java"
    ]
  }
}
```

This example is a schema illustration, not a reviewed plan or file authorization. Existing test references in implementation-task coverage are explicitly read-only and excluded from editable scope. Model proposals editing them are rejected before writes. Additive test creation must have its exact path prospectively declared. Version 1 supports one implementation task and at most one additive test file.

The test-link adapter requires the same workspace and execution receipt, a validated source transaction, matching unchanged original project hashes, an authorized new test hash, passing validation, and ordered test-scope approval, creation, and exit audit records. The release-evidence verifier rechecks this linkage rather than trusting a supplied plan hash. Historical evidence without these receipts continues to report missing implementation-plan approval.

Enable prospective authorization with `python main.py --plan-lineage --design --contract-file CONTRACT_JSON` from `agent-orchestrator/`. This enables the approval/execution adapter; it does not connect all later stages automatically. Authorization receipts use local SQLite/checkpoint authority, not signatures that defend against a privileged database administrator.

## Verification and remaining integration

The focused regression selection passes **278 tests**, including **28 adapter tests** and a new release-evidence test proving that limited scope cannot become engineering-plan approval. Adapter tests use synthetic human decisions, controlled model responses, and synthetic passing-validation records. They exercise real local source transactions and SQLite receipts, but do not establish a live integrated SDLC run or genuine Maven execution for this new adapter. No new Ollama calls, Maven runs, Java production changes, release approvals, or deployment occurred during this milestone. `git diff --check` passes.

Checks include stale plan/requirement/design/workspace bindings, altered original tests/build inventory, approval ordering, authoritative initial/retry prompts, rejected protected-test edits, compensation after binding failure, missing test-stage records, failed validation, and tampered source journals. Read-only checkpoint inspection confirmed both the rejected terminal readiness review and the original engineering-plan approval interrupt.

Full orchestration remains unfinished. The next integration must connect additive test generation to the approved implementation transaction without reapplying a separately authorized Clock patch, then connect genuine Maven evidence, human test-results review, documentation, readiness, and final review through the verified receipts. Revised recovery plans also need deliberate authorization renewal rather than implicit receipt reuse. Parallel execution, metrics, expanded policy sign-offs, and complete reproducible SDLC scenarios remain subsequent work.

The retained Clock demonstration remains an exact human-reviewed, assistant-authored implementation patch. Its LLM produced constrained test specifications rendered by trusted code; documentation generation similarly selected vetted facts. None of those historical provenance distinctions or approvals is upgraded by this adapter.
