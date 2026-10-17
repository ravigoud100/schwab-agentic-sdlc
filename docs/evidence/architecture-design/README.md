# Architecture/design and stage gates — October 16, 2026

This milestone adds a read-only architecture stage before task planning. The stage consumes the previously approved custom-expiration requirement contract. Architecture generation does not authorize source edits.

Entry requires resolved structured requirements, the matching human-approved obligation contract, codebase analysis, an initialized workspace marker, and no unfinished source transaction. Exit requires a strict architecture artifact containing API contracts, typed request fields and validation rules, components and interactions, data-model changes, compatibility risks with mitigations, architectural decisions with rationales, and complete structured coverage of approved implementation/test obligations. References and existing Java paths are validated. API fields must reflect optional UTC input, and API validation rules must declare future-only validation. Test coverage must reference Java test components.

Valid artifacts reach `DESIGN_APPROVAL`. All design-enabled changes require review; a model cannot skip review by declaring low impact. Design approval does not approve implementation. Planning only follows explicit design approval and receives the artifact; its resulting plan is bound to the approved design hash. Requirement changes invalidate old contracts, designs, plans, and approvals.

## Verification scope

The focused selection passes **180 tests**, including **22 design tests**. They cover structural and semantic gate failures, invalid references and paths, API mismatches, entry prerequisites, bounded regeneration and exhaustion, SQLite failure events, same-thread design review, rejection before planning, source preservation, approval binding, requirement-change invalidation, and the approved-design handoff to planning. Previous implementation, recovery, repair, rollback, and validation tests remain passing.

Design validation establishes declared architecture coverage and selected structured consistency rules. It does not prove that narrative decisions are complete or correct, that later source edits implement them, or that arbitrary contradictions in prose are detected. Human review and genuine implementation validation remain required.

This is an opt-in rollout: use `python main.py --design --contract-file CONTRACT_JSON`. Existing workflows without `design_required` retain their historical route. New-file authorization, autonomous test generation, documentation generation, release-readiness gates, parallel execution, and every-stage SDLC gating remain later milestones.

## Live acceptance

The real local `qwen2.5-coder:latest` produced a valid architecture artifact in the final branch, passed entry and exit gates, and stopped at `AWAITING_DESIGN_APPROVAL` with next node `design_review`. All eight approved obligations are covered. The design is **not approved**; task planning and implementation have not started. Java hashes match the original snapshot and production checkout, with zero changed Java files. Maven was not executed for this read-only milestone.

Review [the live artifact](success/design.json), [model prompt](success/attempt-1-prompt.txt), [raw response](success/attempt-1-response.json), [persisted state summary](success/result.json), [run-specific audit](success/run-events.json), and [verification summary](summary.json). The approval hash binds the artifact to requirement version 1, the unchanged requirement/decision hash, and the previously reviewed obligation contract. SQLite contains 14 graph checkpoints on thread `d3ea55e8-4661-4a8d-a370-2c14ad42f5ac`. Original databases are under `/private/tmp/schwab-design-test-components/`.

The demonstration forks the earlier approved-contract SQLite database into a separate evidence branch while retaining requirement identity and thread ID. It reuses the original workspace read-only. It is not a claim that every demonstration used one uninterrupted database. Historical audit events preceding `DESIGN_ACCEPTANCE_STARTED` are retained in the full audit export; run-specific exports isolate this milestone.

### Reliability and review issues

The successful branch used one live call, but this was not the first engineering attempt. A [preliminary branch](preliminary/status.json) returned two invalid artifacts (an unresolved data-model reference and test obligations mapped to production components); its third generation was interrupted after the API consistency schema was strengthened. A [second branch](exhausted/result.json) produced three artifacts with invalid test-component coverage, exhausted its retry limit, and stopped with no pending nodes or Java changes. The final prompt explicitly illustrated both production and test components and improved reference diagnostics. Every generated response is retained. No model artifact was hand-edited, and prompts were not changed between attempts within a bounded branch.

The final artifact still copies a stale narrative risk saying the expiration decision has not been approved, despite its correct selected decision and typed obligation coverage. This is a human-review issue retained verbatim; the machine gate does not detect general narrative contradictions. Its HTTP 400 description is a proposed design detail reflecting the earlier codebase analysis, not a newly approved requirement. The API artifact focuses on expiration-related inputs and is not a complete application API specification. These limitations prevent treating schema/coverage success as automatic architecture approval.

Reproduce the read-only design run with `python scripts/design_demo.py NEW_DIRECTORY --source APPROVED_CONTRACT_CHECKPOINT_DIRECTORY` from `agent-orchestrator/`. The source checkpoint must already contain resolved requirements and a matching human-approved obligation contract. The runner permits bounded design retries, records all responses, and never resumes design approval.
