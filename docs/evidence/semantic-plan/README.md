# Semantic plan coverage acceptance — October 16, 2026

The human explicitly approved a machine-checkable contract for optional caller-provided future UTC expiration, preservation of missing-expiration behavior, and future/past/equal-to-now/missing test cases. The contract contains four implementation obligations and four test obligations. These were supplied explicitly and reviewed before planning; they were not inferred from prompt keywords or silently invented by the planner. Rejection behavior is specified without inventing an HTTP error code.

The successful bounded planning run rejected a deliberately generic proposal, automatically supplied the validation error to the real local `qwen2.5-coder:latest`, and accepted the next proposal. The live task description explicitly selects custom expiration, UTC, future-only validation, and no-expiration compatibility. Structured coverage maps all eight approved obligations to the implementation task and existing source/test files. The workflow stopped at engineering plan approval. No implementation, source writes, or Maven execution occurred.

| Check | Successful run |
| --- | --- |
| First proposal | Controlled fixture: “Implement URL expiration functionality.”; rejected for missing structured coverage |
| Second proposal | Real model output; all eight obligation specifications covered |
| Automatic feedback | Rejection reason supplied in the next planning prompt |
| Requirement version | 1, unchanged |
| Clarification | A1 = Custom expiration timestamp, unchanged |
| Requirement/decision hash | `1de7e94476528dfd37493b75a48cc91b60b085cf152d34b710f7387139705b2d`, unchanged |
| Approved contract hash | `26214f35a9e30c68f8eef03ca20cc973e696f933930519dce25b7696823432b3` |
| Pending node | `approval` |
| Changed Java files | 0 |

Review [approved contract](success/contract.json), [before state](success/before.json), [injected proposal](success/attempt-1-response.json), [automatic feedback prompt](success/attempt-2-prompt.txt), [live plan](success/plan.json), [result](success/result.json), and [run-specific audit events](success/run-events.json). [Summary](summary.json) records checkpoint counts and matching Java hashes. Each branch forks the previously clarified SQLite checkpoint database, retaining thread `d3ea55e8-4661-4a8d-a370-2c14ad42f5ac`, requirement version, and decision hash. They are separate database branches, not a claim of one uninterrupted database across both demonstrations. The original clarified workspace was reused read-only. Full audit exports also retain historical events preceding each branch.

## Exhaustion and prompt correction

This was not a first-run success. The [initial run](initial-schema-failure/result.json) rejected the injected proposal and both live model regenerations, then stopped with `STOPPED_SEMANTIC_PLAN_INVALID`, no pending nodes, and no source changes. Its prompt incorrectly retained a tasks-only “exact schema” despite requesting coverage elsewhere. That conflict was corrected before the separate successful run. All responses and prompts from the failed branch are retained. There were no manual prompt changes or proposal edits between attempts within either bounded run. The first proposal in both branches is explicitly a fixture, not a spontaneous live-model failure.

Original databases and raw model responses remain under `/private/tmp/schwab-semantic-plan/` and `/private/tmp/schwab-semantic-plan-schema-fixed/`. The source clarified checkpoint is `/private/tmp/schwab-requirements-clarification/`. Later minor bookkeeping changes clear stale approval fields/errors and add an explicit exhaustion event; these historical exports retain the exact earlier recorded fields and rejection events.

## Gate scope and verification

The validator requires exact declared behavior/test specifications, complete unique obligation coverage, implementation-task references, and file mappings within each task's allowlist. Test obligations must map to existing Java test files. All proposed file paths pass the existing Java-source authorization policy before approval. Contract approval binds the contract, requirement version, and requirement/decision hash. Plan approval/execution rechecks both coverage and the accepted plan hash. Missing coverage, ID-only assertions, changed specs, duplicate/unknown obligations, missing task references, and unauthorized file mappings cannot pass by keyword presence or a model's self-assessment.

This is validation of explicit **declared design coverage**, not proof that future generated Java implements those declarations or that the requirement contract is complete. Human plan review and genuine implementation tests remain required. General contradiction detection in free-form task descriptions is not provided. The contract author remains responsible for semantics and completeness.

Machine-contract validation is enabled for workflows supplied with an explicit `obligation_contract` (CLI: `--contract-file PATH`). Legacy workflows without one retain their existing structural gate. Once contract review is used, changing requirements clears its approval and old contract; planning stops until a replacement contract is supplied and reviewed. Coverage-aware implementation recovery beyond this planning gate is not demonstrated here.

The focused regression selection passes **158 tests**: the previous 139 plus 19 semantic-plan tests. These cover generic rejection, successful automatic retry, three-attempt exhaustion, SQLite rejection records, unchanged requirement identity, source preservation, contract rejection, invalid contracts, stale approval bindings, plan tampering, missing contracts after requirement changes, and invalid coverage/file mappings. Existing implementation, repair, rollback, recovery, and validation tests remain passing.

Reproduce with `python scripts/semantic_plan_demo.py NEW_DIRECTORY --source CLARIFIED_CHECKPOINT_DIRECTORY --approved-contract` from `agent-orchestrator/`, only after a human reviews the supplied custom-expiration contract. The runner reuses the approved clarification, injects one generic proposal, allows at most two live regenerations, and never resumes engineering plan approval.
