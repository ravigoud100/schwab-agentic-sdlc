# Integrated test generation and validation — October 16, 2026

Subsequent continuation: the human approved the exact results bundle, documentation passed its gates, and independent readiness remains blocked on security/operational sign-off. The workflow now awaits final human review. See [integrated documentation/readiness evidence](../integrated-documentation-readiness/README.md). The account below retains the state at this earlier milestone.

**The workflow is paused at `AWAITING_TEST_RESULTS_REVIEW` with all 12 real Maven tests passing.** Eight original tests and four generated fixed-UTC boundary tests executed with zero failures, errors, or skips. Test results, documentation, release, and deployment have not been approved. The engineering documentation stage has not run.

## Architecture review and correction

The pre-test review treated constructor delegation as mandatory because the approved design explicitly requires it. The prior direct field initialization was rejected despite passing the original Maven tests. [The review](../integrated-test-review/README.md) and its hash-bound audit record were retained.

The user explicitly requested an AI correction using the remaining third production-generation attempt. The model changed exactly the repository-only constructor body to `this(repository, java.time.Clock.systemUTC());`, retaining its `@Autowired` annotation and the explicit Clock overload. No other bytes changed from the reviewed second proposal. All eight original Maven tests passed again. [Correction evidence](../integrated-test-review/correction/summary.json), [the exact diff](../integrated-test-review/correction/correction.diff), and [the third raw model response](../integrated-test-review/correction/implementation-3-response.txt) preserve the result. No Java was hand-corrected.

## Test-stage failure and explicitly approved recovery

The first approved test-specification call returned the four **implementation** obligation IDs instead of the four **test** IDs. The existing validator rejected that response before creating a test file. The source transaction was rolled back, restoring the complete original inventory. [The failed response, prompt, result, and audit](../integrated-test-review/test-generation-failure/) are preserved. This was a generation/schema failure; no combined Maven run occurred in that attempt.

The prompt was corrected to provide the exact filtered test payload: `test-future`, `test-past`, `test-boundary`, and `test-missing`, with their unchanged approved specifications. Implementation IDs remain prohibited; no validation rule or assertion was relaxed. There is no automatic test-generation retry loop.

Because all three production-generation attempts had been used, the assistant requested a separate human decision rather than resetting the counter. The user approved [one exact-source recovery](../integrated-test-review/test-recovery-proposal.json): replay the already reviewed third model proposal byte-for-byte, bind a new transaction/execution receipt to the unchanged approved plan, validate the original tests, and authorize one new constrained test-specification call. The recovery does **not** call the implementation model. Its source SHA-256 remains `f39cf52ddd01f17d03cc957b8fc5e83632796e50b4425c11ff7559a670085318`.

The new source transaction and test scope have fresh receipts; the approval for the rolled-back transaction was not silently reused. [The new scope](approved-replay-test-scope.json) and [audit](audit.json) record this explicitly approved continuation on the same workflow/thread/workspace. Three source generations remain three source generations; there are four source applications, including the separately approved deterministic replay. Audit records distinguish them.

## Genuine Maven results

| Execution | Outcome |
| --- | --- |
| Original baseline | 8 original tests passed |
| Exact corrected-source replay | 8 original tests passed |
| Combined replayed implementation and generated tests | 12 tests passed; 0 failures/errors/skips |

The real local model returned valid constrained case JSON on the approved recovery call. A trusted deterministic renderer created only [GeneratedExpirationBoundaryTest.java](GeneratedExpirationBoundaryTest.java). It uses `Clock.fixed(Instant.parse("2030-01-01T00:00:00Z"), ZoneOffset.UTC)` and asserts future expiration retention, past and equal-now rejection with HTTP 400 and no repository save, and null/no-expiration behavior. Existing integration tests retain HTTP 201/400/404/410, active redirect, and click-count checks.

Review [the generated specification](generation-response.json), [its exact prompt](generation-prompt.txt), [combined Maven log](positive/maven.log), [combined cases](positive/result.json), and Surefire XML in `positive/`. [The replay source log](source-replay-1/maven.log) independently validates the reapplication before test generation. These are actual Java 21, offline `./mvnw ... clean test` subprocess executions. No controlled negative fixture or unrestricted novel Java test synthesis is claimed in this milestone.

## Lineage and preservation

[The summary](summary.json) and [verified engineering bundle](verified-bundle.json) bind requirement version/hash, approved contract/design/plan, original authorization, new execution receipt, source/test hashes, Maven reports, test scope, and transaction. The canonical nested LangGraph checkpoint is at test-results review, and its bundle hash matches independent re-verification. All stages use workflow/thread `e43f07a6-4824-4f49-865f-69ae19899ca1`, the original isolated workspace, and the same SQLite checkpoint/audit databases.

The final project inventory differs from the independent original snapshot only at `UrlService.java` and the single authorized additive test file. Existing tests, build configuration, and all other protected files remain byte-identical. Production-checkout Java remains unchanged. The replayed production source matches the third raw model proposal and source journal bytes exactly. The generated test matches the vetted renderer exactly.

[Source journals](transaction-journals.json) retain three `rolled_back` transactions and one final `validated` transaction. [The creation journal](integrated-test-journal.json) is `validated`. `ENGINEERING_TESTS_BOUND` links the actual generated file and passing validation to the new replay execution, not a different workspace or plan. Prior failed proposals and checkpoints have not been overwritten. [Persisted state](result.json) retains the pending human test-results interrupt.

## Regression checks and limits

The complete automated Python suite passes **347 tests**. New checks cover mandatory delegation, green-Maven/design-mismatch rejection, retained attempt accounting, single-use correction approval, nested checkpoint resume, filtered test-only obligations, rejection of the actual failed response, and exact-source replay hash/approval/budget protections. Earlier automated tests remain passing. `git diff --check` and Python compilation checks pass.

This is a bounded expiration-profile integration using imported approved requirements and an explicitly labeled assistant-authored plan fixture. Production implementation was live model-generated; test specifications were live model-generated; JUnit was rendered by trusted code. The source design correction and later exact replay were explicitly human-directed. This is not a claim of a new requirement traversing every SDLC stage autonomously, general test synthesis, automatic post-budget source repair, or production release readiness.

Original databases, snapshots, workspace and raw reports remain under `/private/tmp/schwab-integrated-sdlc-fixed/`. Later workflow stages must wait for a new explicit test-results decision. The earlier four-file plan and historical blocked release decisions remain unchanged.
