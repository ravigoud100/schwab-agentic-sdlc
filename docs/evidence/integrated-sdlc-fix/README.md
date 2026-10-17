# Live integrated compilation recovery — October 16, 2026

Later review corrected the mandatory constructor-delegation mismatch through the user-approved third AI attempt. After a separately approved exact-source/test-stage recovery, [the integrated validation milestone](../integrated-test-validation/README.md) reached `AWAITING_TEST_RESULTS_REVIEW` with 12 real Maven tests passing. The source-stage snapshot below is historical; its original evidence remains intact.

**The requested source-stage success criterion was met:** real AI-generated Java passed genuine Maven validation and the workflow is paused at `AWAITING_TEST_GENERATION_APPROVAL`. Test generation, release, and deployment were not approved or executed.

The fix changes the implementation response transport, not the approved requirements, production-file scope, transaction protections, or three-attempt limit. [Root-cause analysis](ROOT_CAUSE.md) and [verified historical findings](failure-analysis.json) explain both earlier failed runs. Their original evidence and databases remain intact.

## Live outcome

| Stage | Result |
| --- | --- |
| Requirements/design/plan | Same requirement identity and exactly the previously reviewed design/plan; fresh workspace-bound authorization under the user's instruction to rerun the approved workflow |
| Baseline Maven | Eight original tests passed |
| Attempt 1 | Complete-file proposal compiled without the earlier malformed insertion, but omitted `random` and `CHARACTERS`; Maven reported three unresolved-symbol errors |
| Rollback and recovery | Original source restored; full compiler log, materialized failed source, original source, approved design/task/contract supplied automatically to regeneration |
| Attempt 2 | Real local `qwen2.5-coder:latest` restored the fields; Maven `clean test` passed all eight original tests, including Spring context startup |
| Current state | `AWAITING_TEST_GENERATION_APPROVAL`; same thread and workspace; explicit human interrupt pending |
| Source transaction journals | Attempt 1 `rolled_back`; attempt 2 `applied`, pending later combined implementation/generated-test validation |
| Changed production files | Only `UrlService.java`, in the isolated workspace |
| Existing tests/build/other project files | Byte-identical to the independent original snapshot |
| New tests | None created |
| Production checkout | Java files unchanged |

[Summary](summary.json) records the requirement/design/contract/plan hashes, new authorization receipt, transaction IDs, Maven cases, pending interrupt, and SQLite checkpoint counts. Review the [actual diff](implementation.diff), [first model response](implementation-1-response.txt), [failed Maven log](implementation-1/maven.log), [automatic recovery prompt](implementation-2-prompt.txt), [corrected model response](implementation-2-response.txt), [successful Maven log](implementation-2/maven.log), [audit](audit.json), [journals](transaction-journals.json), and [persisted result export](result.json). The successful response's content matches both the applied journal bytes and workspace bytes exactly. No generated Java was hand-corrected or replaced with the historical Clock patch.

This was a genuine bounded recovery: two attempts in one workflow, not two manually refined source implementations. The prompt and code were not changed between these two attempts. Both failing and passing proposals, prompts, usage metadata, logs, XML reports, and journals are retained. `implementation-1` has no Surefire test results because compilation failed; it is not counted as executing tests. `implementation-2` contains all eight original passing test cases with no skips/errors/failures.

## Implementation changes

The integrated path selects `complete_files`. Its provider must return `{"files":[{"path":"...","content":"complete Java source"}]}`. The executor converts that into one exact whole-file replacement per approved production file, using original bytes it read and authorized itself. The adapter does not write Java or repair model code. Duplicate, missing, protected-test, unauthorized-path, malformed, and unchanged responses fail before transaction application. Legacy `edits` mode remains available to existing callers and mocks.

Both generation and recovery receive complete original sources (including imports and existing declarations), selected clarification answers, approved design, contract, task, plan, and authorization hash. Recovery receives the actual full compiler output and materialized failed source separately from the restored original. This removes ambiguous model-selected constructor insertion boundaries and avoids truncating away compiler evidence.

The source gate and transaction behavior remain intact. Failure restores the approved original baseline; retry rechecks the unchanged task/plan authorization. No materially different task or file scope can reuse the receipt. The final transaction intentionally remains `applied` until generated tests and combined validation are authorized and completed. Passing original tests alone does not grant test-stage or release approval.

The demo export now reports the persisted nested workflow status separately from the parent engineering node's older status while a subgraph interrupt is pending. The canonical subgraph checkpoint and audit both record `AWAITING_TEST_GENERATION_APPROVAL`.

## Verification and limits

The complete automated Python regression suite passes **318 tests**, preserving the prior 299 and adding 19 regression cases. Run `.venv/bin/python -m pytest -q` from the repository root. Pytest configuration excludes six pre-existing standalone live smoke scripts that execute model/worktree operations at import time and have no pytest test functions; they remain available for explicit manual execution. No automated regression tests were removed or skipped. `git diff --check` and Python compilation checks pass.

New tests replay all seven historical transaction snapshots, cover complete-file scope validation and byte-exact compensation, verify transfer of full failing/original sources and diagnostics, exercise same-plan correction to the test-generation interrupt, and retain the observed missing-fields regression. Real Maven evidence comes from the separate live run described above, not synthetic graph-test records.

Maven establishes compilation and the eight retained tests, not complete proof of the approved architecture or boundary behavior. The generated default constructor directly initializes the repository and `Clock.systemUTC()` rather than using the constructor-delegation spelling requested in the design prose; the repository-only overload is correctly autowired and Spring startup passes. This implementation detail remains visible for review. Explicit fixed-clock boundary tests have not been generated or run; their authorization is the next pending gate. No semantic-completeness, arbitrary-source-generation reliability, or release-readiness claim is made.

The runner uses the unchanged, explicitly labeled assistant-authored plan fixture and imports earlier approved requirements/contract. Production Java is live model-generated. This is not a claim that a completely new requirement traversed all SDLC stages from START.

Original SQLite databases, independent snapshots, workspace and raw reports remain under `/private/tmp/schwab-integrated-sdlc-fixed/`. The earlier four-file engineering plan remains unapproved; earlier blocked release decisions are unchanged. Do not resume the pending test-generation gate without explicit human approval.
