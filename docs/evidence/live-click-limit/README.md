# Live single-file implementation acceptance — October 16, 2026

Two user-approved runs used the real local `qwen2.5-coder:latest` Ollama model, real Maven validation, Git-isolated workspaces, SQLite LangGraph checkpoints, and SQLite audit logging. Model responses and Maven results were not mocked. The rollback run reused the same live-generated plan and analysis in a fresh baseline workspace and requested a new live implementation edit.

The requirement was to add `hasReachedClickLimit(long maxClicks)` to `ShortUrl`: reject nonpositive limits with `IllegalArgumentException`, otherwise compare the existing click count with the limit. This method was absent initially. Independent acceptance tests were installed in the demonstration baseline before AI execution, using reflection so the baseline compiled and failed specifically on the missing method. The production checkout and its test files were not modified by the demonstration.

| Evidence | Normal validation | Deliberate failure |
| --- | --- | --- |
| Maven tests | 11 passed, zero failures/errors | 11 run, one deliberate probe failure, zero errors |
| Maven exit code | 0 | 1 |
| Final state | `AWAITING_FINAL_HUMAN_REVIEW` | `STOPPED_ROLLED_BACK` |
| Files changed at termination | Only isolated `ShortUrl.java` | None; original bytes restored |
| SQLite checkpoints | 12 | 7 |
| Final-review audit event | Present | Absent |

The failure probe was part of the preinstalled test fixture and activated with `-Ddemo.forceValidationFailure=true`; the executor did not alter tests to manufacture failure. The other ten tests, including the feature checks, passed in that run. The initial baseline had 11 tests with one failure and one missing-method error, independently demonstrating the absence of the feature.

Review [the generated plan](plan.json), [the implementation diff](implementation.diff), [the baseline result](baseline.json), [normal-run result](success-result.json), [normal-run audit export](success-audit.json), [rollback result](rollback-result.json), [rollback audit export](rollback-audit.json), and [checkpoint summary](summary.json).

Original SQLite databases, retained worktrees, source snapshots, and Maven Surefire reports are under `/private/tmp/schwab-live-click-limit/` and `/private/tmp/schwab-live-click-limit-rollback/`. These directories are local temporary evidence; the portable JSON exports and diff are retained here. Raw model implementation response payloads were not separately archived; the executor parsed and validated them as exact-edit JSON before applying them, and the resulting source diff is recorded.

Live planning initially returned fenced, schema-invalid output. Those attempts made no source edits. Strict initial-plan parsing and the demo's Ollama JSON-output mode produced the valid plan presented to the user for approval. The legacy prose fallback remains available to legacy parser callers, but initial workflow planning now requires valid structured JSON.

This is a bounded single-file demonstration. It does not establish multi-file implementation, structured requirement clarification, arbitrary requirement success, or complete SDLC orchestration. Reaching final human review does not publish or merge the generated implementation.
