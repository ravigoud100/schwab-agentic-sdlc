# Final authorized brownfield attempt — October 16, 2026

**Terminal outcome: `STOPPED_RETRY_EXHAUSTED`. No implementation attempts remain.** The live model responded successfully, but its generated controller failed genuine Java compilation. Results review was not reached. No results, merge, release, or deployment was approved.

## Restart and lifecycle repair

The user explicitly authorized stopping the diagnostic monitor, restarting Ollama, and using only attempt 3 under the existing continuation. The monitor was terminated before its scheduled diagnostic request. No brownfield generation process was active. Ollama's application quit handler returned “User canceled”; the identified app/server received SIGTERM and were restarted. The ten-minute idle observation from the previous request was superseded and stopped after about six minutes, not represented as completed.

The unchanged `qwen2.5-coder:latest` loaded and answered a harmless [restart probe](restart-probe.json) in 2.85 seconds. Before implementation, `/api/ps` reported approximately 6.43 GB model allocation with context 32,768; macOS reported 21% system-wide free memory and approximately 2,654 MiB swap use. These measurements are not total process memory or a proof of long-term resource stability.

Request handling now holds an exclusive kernel file lock through dispatch and response persistence. Reconciliation refuses an active owner. Response bytes are written with no overwrite and fsynced before ledger completion. A late response is retained even if the ledger has been changed externally; exception handling does not reopen a consumed attempt or mask its original failure. Reservation uses an immediate SQLite transaction. The adapter checks the consumed ordinal sequence and the three-attempt bound.

The actual process session was retained and polled through exit code 0. Tool yields did not trigger interruption reconciliation. This completed live request took **56.235 seconds**, evaluated 2,722 prompt tokens and generated 1,303 tokens. [Raw response](response.txt) matches the ledger response hash; [usage metadata](metadata.json) and [server log](server-tail.log) record normal completion with no truncation. Java was not hand-corrected.

## Authorization and execution

Workflow/thread `e4e0d578-28db-40a3-944c-32a60be1722c` and its existing workspace were reused. The original plan hash, policy digest, exact three-file scope, production hashes, complete previously captured project inventory, and continuation receipt were verified before dispatch. Original attempt rows 1 and 2 remain byte-for-byte equivalent as database records, both `INTERRUPTED` and consumed. Their historical audit prefix is unchanged.

The canonical checkpoint still had pending `implement` work and an obsolete attempt counter of zero. An audited checkpoint update reconciled that counter to two before resuming the same graph with `invoke(None)`. No new workflow, approval, or three-attempt budget was fabricated. The continuation explicitly allowed ordinals 2 and 3. [Audit](audit.json), [ledger](attempts.json), and [result](result.json) preserve this distinction. Attempt 3 is `COMPLETED` for model generation; engineering validation failed separately.

The model received complete original production Java sources, the reviewed requirement/architecture packet, and execution authorization. The earlier missing-Map failure was labeled as historical diagnostic context, not a current failure. The generated proposal changed only service/controller through one source transaction. The sole additive test was separately journaled and policy-checked.

The status test renderer's malformed quoting was replaced with a valid deterministic test harness covering missing-code 404, read-only annotation, expiration states, unchanged status click counts, and normal redirect increments. [Its source](UrlStatusIntegrationTest.java) is trusted harness code, not live AI test generation. Existing tests and build files were not changed. Because production compilation failed first, these new tests did **not** execute and do not establish behavior coverage for this implementation.

## Observed failure and compensation

[Maven](maven.log) failed at `UrlController.java:[71,43]`: `cannot find symbol`, variable `Instant`. The model introduced an unqualified reference without the required import. See the [failed proposal diff](failed-proposal.diff). No tests executed in this failed build. The final attempt cannot regenerate merely because this error appears straightforward.

The generated test's current hash was checked before deletion, and both production files were restored through the source transaction. [Both journals](journals.json) are `rolled_back`. [Independent verification](verification.json) confirms all original files match their pre-attempt hashes and the additive test is absent. A separate genuine Maven run against the restored baseline passed **8 tests, zero failures/errors/skips**; [log](restored-baseline-maven.log) and [XML](restored-baseline-surefire/) are retained. This baseline pass is not success of the failed feature.

## Verification and limits

The full Python suite passed **476 tests**, including seven new ownership/response/checkpoint tests. They cover active-owner rejection, durable response-before-completion ordering, late response retention, failure after response receipt, refusal to redispatch consumed ordinals, kernel-lock release on process exit, and checkpoint continuation at only attempt 3 with exhaustion/compensation. Existing tests remain passing. `git diff --check` passes.

This remains a bounded scenario runner. Kernel ownership protects cooperating local processes; privileged database writers can bypass it. Late responses are preserved, not automatically adopted into engineering state. Arbitrary mid-transaction crashes and noncooperating writers can still require manual recovery. No successful status-feature validation or general generation-reliability claim follows from a responsive model or passing baseline.

Original databases, pre-attempt SQLite backups, workspace and journals remain under `/private/tmp/schwab-assessment-brownfield-ledger/`. Portable exports include all final-attempt prompts, response, diagnostics, receipts, and journals. Older failed workflows were not restarted or overwritten. Further implementation requires a new explicit decision; this execution is terminal at its approved retry limit.
