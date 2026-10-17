# Live coordinated API acceptance — October 16, 2026

The user approved the generated two-file plan for a normal run and a separate deliberate-failure run. Both final runs used real local `qwen2.5-coder:latest` calls, real Maven tests, separate Git workspaces, SQLite checkpoints, and SQLite audit logging. Generated Java edits were not hand-corrected or merged into the production checkout.

The enhancement adds `GET /api/urls/{shortCode}/status` with `shortCode` and `expired` JSON fields. `UrlController` delegates to the new read-only `UrlService.getStatus` method. Missing codes return 404; unset/future/past expirations and unchanged click counts are independently tested. Only those two existing Java files are authorized for the implementation task.

| Check | Normal run | Forced-failure run |
| --- | --- | --- |
| Maven | 13 tests, zero failures/errors | 13 tests, one deliberate probe failure, zero errors |
| Final state | `AWAITING_FINAL_HUMAN_REVIEW` | `STOPPED_ROLLED_BACK` |
| Transaction journal | `validated` | `rolled_back` |
| Java files changed at termination | Approved service and controller only | None; both original byte sequences restored |
| File checkpoints | Two, recorded before application | Two, recorded before application |
| SQLite workflow checkpoints | Seven | Seven |
| Final-review audit event | Present | Absent |

The failure is triggered by a preinstalled acceptance-test probe via `-Ddemo.forceValidationFailure=true`. All other 12 tests passed in that run, including the read-only transaction check. The executor did not change the fixture or build configuration to induce failure. The final runs used the same fixture, installed before generation in each fresh baseline snapshot.

Review [the approved plan](plan.json), [the normal model proposal](success-proposal.json), [its source diff](success-implementation.diff), [normal Maven cases](success-maven-tests.json), [the normal result](success-result.json), and [normal audit events](success-audit.json). Compare [the forced-failure proposal](rollback-proposal.json), [its pre-rollback source diff](rollback-implementation.diff), [forced-run Maven cases](rollback-maven-tests.json), [the rollback result](rollback-result.json), and [rollback audit events](rollback-audit.json). The diffs describe the actual generated proposal; the rollback workspace currently contains the originals.

[Normal](success-journal.json) and [rollback](rollback-journal.json) journals retain each original content snapshot, original hash, generated content, expected hash, and transaction ID. `FILE_CHECKPOINT_CREATED` audit events identify both files and their checkpoint IDs before `TRANSACTION_APPLIED`. The final SQLite state was checked against the result and journal, including all file checkpoints and absence of pending graph nodes. [Summary](summary.json) records workflow and transaction identifiers.

The retained databases, source snapshots, workspaces, and original Surefire XML reports are under `/private/tmp/schwab-live-status-api-success-11/` and `/private/tmp/schwab-live-status-api-rollback-final/`. The portable JSON exports, proposals, journals, test summaries, and diffs are retained here.

## Reliability limits observed

This was not a first-attempt success. There were **11 unsuccessful normal attempts before the successful twelfth attempt**. [Attempt history](attempts.json) records all outcomes and whether Maven actually ran, using audit events rather than inherited diagnostics. The earlier attempts exposed schema/whitespace problems, missing imports, incorrect `Optional` handling, malformed method insertion, and a missing read-only transaction annotation. Unapplied invalid proposals made zero source writes. Applied proposals that failed compilation or tests restored both files and never reached final review. One unsuccessful attempt used the already-installed `llama3.1:8b`; the final two runs used the coding model.

The assistant orchestrated fresh-workspace retries and refined the generic prompt and diagnostic context. The workflow does not yet automate this entire correction loop. The model generated the edits on each attempt; the assistant did not patch generated Java into passing code. A missing assertion for the already-approved read-only transaction requirement was added to the independent fixture before the final compilation/test correction attempts; the final success and forced-failure runs both used that stronger fixture. An early blocked retry inherited prior Maven diagnostics in its result before prior/current validation fields were separated; the attempt history identifies its actual outcome from audit events.

Transactions stage and validate the entire proposal, serialize cooperating executors with a workspace lock, and use atomic per-file replacements plus whole-set compensation. Durable journals permit recovery of interrupted operations. This does not provide simultaneous multi-file filesystem visibility or guarantee automatic restoration against unrelated concurrent writers or persistent storage failure. Unexpected source hashes stop recovery for review rather than overwrite external edits. Recovery tests cover a failed second replacement, staging errors, interrupted application, checkpoint tampering, validation-launch failure, and whole-set rollback preflight.

The focused regression selection passed **100 tests** after these changes. This evidence establishes a bounded two-file scenario and failure recovery. It does not establish reliable arbitrary-task generation, automatic generic repair orchestration, new-file support, structured clarification, or complete SDLC coverage.
