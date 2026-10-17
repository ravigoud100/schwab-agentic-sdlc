# Audit-backed reliability metrics

Selected retained database branches; not a production reliability estimate.

## controlled_failure_live_recovery

Engineering validation: 1/1 observed outcomes passed (1.0); 0 unknown.
Released workflows evidenced: 0; blocked releases: 0.
Observed retry workflow frequency: 1/1 (1.0).
Observed rollback workflow frequency: 1/1 (1.0).
Completed-incident MTTR: 72.414036 seconds.
Mean complete workflow latency: unavailable seconds (0 samples).

## controlled_validation_failure_live_model

Engineering validation: 0/1 observed outcomes passed (0.0); 0 unknown.
Released workflows evidenced: 0; blocked releases: 0.
Observed retry workflow frequency: 0/1 (0.0).
Observed rollback workflow frequency: 1/1 (1.0).
Completed-incident MTTR: unavailable seconds.
Mean complete workflow latency: 29.458694 seconds (1 samples).

## deterministic_parallel_fixture

Engineering validation: 1/2 observed outcomes passed (0.5); 0 unknown.
Released workflows evidenced: 0; blocked releases: 0.
Observed retry workflow frequency: 0/2 (0.0).
Observed rollback workflow frequency: 1/2 (0.5).
Completed-incident MTTR: unavailable seconds.
Mean complete workflow latency: unavailable seconds (0 samples).

## retained_live_integrated

Engineering validation: 1/3 observed outcomes passed (0.3333333333333333); 0 unknown.
Released workflows evidenced: 0; blocked releases: 1.
Observed retry workflow frequency: 3/3 (1.0).
Observed rollback workflow frequency: 3/3 (1.0).
Completed-incident MTTR: 1211.328118 seconds.
Mean complete workflow latency: unavailable seconds (0 samples).

## a78de73a-00cd-4e60-b3fa-5c00a01ae650 (retained_live_integrated)

Status: STOPPED_INTEGRATED_STAGE. Engineering: failed. Released: False.
Automatic retries observed: 2; unique rollbacks: 4.
MTTR: unavailable — No completed failure-to-engineering-validation recovery interval.
End-to-end latency: unavailable — Exact workflow start or terminal completion missing; pending review is not completion.
Measured review-ready wait: 4.201788 seconds.
Explicit agent execution intervals (union): unavailable — No complete explicit agent start/end pairs.

Unavailable stage timings: requirements, codebase, planning, design, implementation, parallel_proposal, parallel_validation, test_generation_boundary, combined_validation_boundary, documentation_boundary, readiness_boundary.

| Measured stage | Seconds | Evidence |
| --- | --- | --- |
| source_validation_boundary | 1.765231 | audit:25 → audit:26 |
| source_validation_boundary | 4.409182 | audit:34 → audit:35 |
| source_validation_boundary | 1.467037 | audit:43 → audit:44 |

## 1a500baf-88e5-4f73-92ed-6c1e478d6641 (retained_live_integrated)

Status: STOPPED_INTEGRATED_STAGE. Engineering: failed. Released: False.
Automatic retries observed: 2; unique rollbacks: 3.
MTTR: unavailable — No completed failure-to-engineering-validation recovery interval.
End-to-end latency: unavailable — Exact workflow start or terminal completion missing; pending review is not completion.
Measured review-ready wait: 41.136304 seconds.
Explicit agent execution intervals (union): unavailable — No complete explicit agent start/end pairs.

Unavailable stage timings: requirements, codebase, planning, design, implementation, parallel_proposal, parallel_validation, test_generation_boundary, combined_validation_boundary, documentation_boundary, readiness_boundary.

| Measured stage | Seconds | Evidence |
| --- | --- | --- |
| source_validation_boundary | 1.572134 | audit:13 → audit:14 |
| source_validation_boundary | 1.593476 | audit:22 → audit:23 |
| source_validation_boundary | 1.768036 | audit:31 → audit:32 |

## e43f07a6-4824-4f49-865f-69ae19899ca1 (retained_live_integrated)

Status: STOPPED_RELEASE_BLOCKED. Engineering: passed. Released: False.
Automatic retries observed: 2; unique rollbacks: 3.
MTTR: 1211.328118 seconds.
End-to-end latency: unavailable — Exact workflow start or terminal completion missing; pending review is not completion.
Measured review-ready wait: 1537.25175 seconds.
Explicit agent execution intervals (union): unavailable — No complete explicit agent start/end pairs.

Unavailable stage timings: requirements, codebase, planning, design, implementation, parallel_proposal, parallel_validation.

| Measured stage | Seconds | Evidence |
| --- | --- | --- |
| source_validation_boundary | 4.848103 | audit:13 → audit:14 |
| source_validation_boundary | 4.961832 | audit:22 → audit:23 |
| source_validation_boundary | 4.756812 | audit:34 → audit:35 |
| documentation_boundary | 99.273587 | audit:54 → audit:58 |
| test_generation_boundary | 16.776433 | audit:47 → audit:49 |
| combined_validation_boundary | 4.896287 | audit:49 → audit:50 |
| readiness_boundary | 0.022094 | audit:58 → audit:59 |

## 309b0b75-72f2-4ad3-8df6-6b520c88ab52 (controlled_failure_live_recovery)

Status: AWAITING_FINAL_HUMAN_REVIEW. Engineering: passed. Released: False.
Automatic retries observed: 1; unique rollbacks: 1.
MTTR: 72.414036 seconds.
End-to-end latency: unavailable — Exact workflow start or terminal completion missing; pending review is not completion.
Measured review-ready wait: unavailable — No complete reliable review-ready/decision pairs.
Explicit agent execution intervals (union): 33.176574 seconds.

Unavailable stage timings: requirements, codebase, planning, design, parallel_proposal, parallel_validation, source_validation_boundary, test_generation_boundary, combined_validation_boundary, documentation_boundary, readiness_boundary.

| Measured stage | Seconds | Evidence |
| --- | --- | --- |
| implementation | 0.003868 | audit:5 → audit:10 |
| implementation | 33.172706 | audit:21 → audit:26 |

## f9bafc7d-7f6b-4889-9090-439e559eb79c (controlled_validation_failure_live_model)

Status: STOPPED_ROLLED_BACK. Engineering: failed. Released: False.
Automatic retries observed: 0; unique rollbacks: 1.
MTTR: unavailable — No completed failure-to-engineering-validation recovery interval.
End-to-end latency: 29.458694 seconds.
Measured review-ready wait: unavailable — No complete reliable review-ready/decision pairs.
Explicit agent execution intervals (union): unavailable — No complete explicit agent start/end pairs.

Unavailable stage timings: requirements, codebase, planning, design, implementation, parallel_proposal, parallel_validation, source_validation_boundary, test_generation_boundary, combined_validation_boundary, documentation_boundary, readiness_boundary.

## parallel-fixture-success (deterministic_parallel_fixture)

Status: AWAITING_PARALLEL_RESULTS_REVIEW. Engineering: passed. Released: False.
Automatic retries observed: 0; unique rollbacks: 0.
MTTR: unavailable — No completed failure-to-engineering-validation recovery interval.
End-to-end latency: unavailable — Exact workflow start or terminal completion missing; pending review is not completion.
Measured review-ready wait: unavailable — No complete reliable review-ready/decision pairs.
Explicit agent execution intervals (union): 25.006181 seconds.

Unavailable stage timings: requirements, codebase, planning, design, implementation, source_validation_boundary, test_generation_boundary, combined_validation_boundary, documentation_boundary, readiness_boundary.

| Measured stage | Seconds | Evidence |
| --- | --- | --- |
| parallel_proposal | 0.008027 | audit:6 → audit:7 |
| parallel_proposal | 0.008135 | audit:5 → audit:8 |
| parallel_validation | 24.998046 | audit:14 → audit:15 |

## parallel-fixture-compilation-failure (deterministic_parallel_fixture)

Status: STOPPED_PARALLEL_ROLLED_BACK. Engineering: failed. Released: False.
Automatic retries observed: 0; unique rollbacks: 1.
MTTR: unavailable — No completed failure-to-engineering-validation recovery interval.
End-to-end latency: unavailable — Exact workflow start or terminal completion missing; pending review is not completion.
Measured review-ready wait: unavailable — No complete reliable review-ready/decision pairs.
Explicit agent execution intervals (union): 0.008506 seconds.

Unavailable stage timings: requirements, codebase, planning, design, implementation, parallel_validation, source_validation_boundary, test_generation_boundary, combined_validation_boundary, documentation_boundary, readiness_boundary.

| Measured stage | Seconds | Evidence |
| --- | --- | --- |
| parallel_proposal | 0.006597 | audit:6 → audit:7 |
| parallel_proposal | 0.008506 | audit:5 → audit:8 |

Timing coverage is partial. Stage intervals, unresolved incidents, missing review boundaries, policy checks, provenance and source hashes are in the JSON report. Unattributed elapsed time is not agent execution time. Historical databases were opened read-only.
