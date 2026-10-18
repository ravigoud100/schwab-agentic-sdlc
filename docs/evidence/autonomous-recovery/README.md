# Autonomous failure recovery acceptance — October 16, 2026

One LangGraph workflow recovered from a controlled Java compilation failure without manual source edits, prompt changes, fresh-workspace retries, or manual diagnostic forwarding between its attempts.

The test starts from the previously reviewed status API plan for `UrlService.java` and `UrlController.java`. The first proposal is the recorded live-generated proposal from the earlier successful demonstration, with **one explicitly seeded defect**: `java.util.Map` is misspelled as `java.util.Mapp` in the service return type. It is a controlled model-output fixture, not a claim that Ollama spontaneously produced this fault during the test. Recovery replanning and corrected edit generation use real local `qwen2.5-coder:latest`; every Maven invocation is genuine.

| Stage | Recorded outcome |
| --- | --- |
| Initial approval | Same task definitions as the exact API plan previously approved by the user; approval gate resumed under that existing authorization |
| Attempt 1 | Both proposed files checkpointed and applied; real Maven compilation fails on `java.util.Mapp` |
| Automatic diagnosis | Classified `java_compilation`; proposal, raw response, compiler errors, approved tasks, and source hashes captured |
| Automatic rollback | Both files restored to their original hashes before replanning or retry |
| Recovery plan | Real model creates version 2 and a targeted correction strategy |
| Approval decision | Task definitions remain identical; existing approval retained without an unnecessary new interrupt |
| Attempt 2 | Real model generates corrected structured edits; both files checkpointed and applied again |
| Required validation | All 13 real Maven tests pass, including expiration, 404, click-count, and read-only transaction checks |
| Final state | `AWAITING_FINAL_HUMAN_REVIEW`, one retry, two changed Java files, no pending nodes |

All stages share workflow ID `309b0b75-72f2-4ad3-8df6-6b520c88ab52`, one workspace, one audit database, and one LangGraph checkpoint database. SQLite contains 13 graph checkpoints. The workspace retains one `rolled_back` transaction journal for attempt 1 and one `validated` journal for attempt 2. Four `FILE_CHECKPOINT_CREATED` events link the two files to their checkpoints across both attempts. The production Java checkout remains unchanged.

Review [summary](summary.json), [full workflow result](result.json), [audit events](audit.json), [classified failure evidence](failure-context.json), [transaction journals](transaction-journals.json), [final Maven cases](maven-tests.json), and [the resulting source diff](implementation.diff). Compare [the initial plan](initial-plan.json), [injected proposal](injected-proposal.json), [live recovery plan](recovery-plan-1.json), and [live correction](live-correction-1.json). The actual [replanning prompt](recovery-plan-1-prompt.txt) and [regeneration prompt](live-correction-1-prompt.txt) show that failure evidence reached both model calls automatically. The failure evidence hash appears in the replanner input and audit trail.

Original databases, source snapshots, and Surefire reports remain under `/private/tmp/schwab-autonomous-recovery/`. The exports here are portable evidence. The separate [baseline](baseline.json) verifies that the status functionality was absent before either attempt; it is not counted as an implementation attempt.

## Recovery controls and verification limits

The generic workflow classifies model-output errors, exact-edit conflicts, Java compilation errors, test failures, infrastructure failures, and authorization failures. Recoverable errors enter targeted replanning only after source restoration. Unexpected source hashes, authorization violations, unavailable infrastructure, invalid recovery plans, or blocked rollback stop safely. The existing `MAX_RETRIES=2` permits at most three implementation attempts including the initial attempt. Retry count, failure evidence, candidates, approval decisions, and plan versions are persisted in graph state.

Materiality is computed from task definitions, not a model-provided approval flag. Changes to task descriptions or file scope require a fresh `RECOVERY_PLAN_APPROVAL` interrupt. Execution-type changes that bypass implementation, task/dependency changes outside supported recovery, and edits to completed tasks are rejected. Unchanged task definitions can regenerate implementation details under existing approval. This live run exercised the unchanged-plan branch. Graph regression tests separately verify materially changed plan approval, rejection before a second write, and retry exhaustion.

The focused regression selection passed **116 tests**. New recovery tests cover deterministic classification, malformed JSON regeneration, evidence transfer, versioning, both approval branches, infrastructure safe stop with restoration, invalid recovery execution type, bounded exhaustion, and preservation of external changes when rollback is blocked.

This is controlled failure injection with real recovery, not evidence of reliable autonomous implementation for arbitrary requirements. Infrastructure classification uses conservative diagnostic rules. Multi-file replacement remains a staged, journaled operation with compensating rollback rather than simultaneous filesystem atomicity. Persistent storage failures or unrelated source writers can require human recovery; their data is never overwritten just to satisfy a rollback claim. The test does not cover new-file creation, arbitrary dependency restructuring, or complete SDLC orchestration.
