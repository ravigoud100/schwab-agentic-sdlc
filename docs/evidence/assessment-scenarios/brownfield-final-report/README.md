# Final brownfield assessment report

**The corrected source is a separately validated offline-assisted implementation with 11 passing Maven tests. Architecture qualification remains unresolved. The live workflow remains `STOPPED_RETRY_EXHAUSTED`; it never reached results review.**

| Assessment dimension | Finding |
| --- | --- |
| Implemented functionality | Status endpoint returns `shortCode` and `expired`; service lookup is read-only and preserves missing-code 404. |
| Original AI compilation | Failed on unresolved `Instant` in the controller. No tests executed. |
| Offline-assisted compilation | Passed after the assistant added only `import java.time.Instant;` in a disposable copy. |
| Genuine test evidence | 11 passed: eight retained original tests and three trusted status tests; zero failures, errors, or skips. |
| Architecture compliance | Consistent with the high-level reviewed packet, but does not satisfy the earlier explicit service-side response instruction. Full compliance is not established. |
| Historical workflow | Terminal retry exhaustion, original files restored, additive test removed; zero attempts remain. |
| Governance | No changed approval, results review, release, merge, or deployment authorization. |

## Architecture review

The [reviewed proposal](reviewed-proposal.json) defines two components, a read-only status method, exposure of the status response, no click increment, and preservation of 404/redirect/analytics behavior. It does **not** explicitly locate response-map construction or expiration evaluation in `UrlService`. Its service task says “Add a read-only status method with no click increment”; the controller task says “Expose the approved status response and preserve 404 behavior.” The corrected implementation meets those declared responsibilities at that level.

However, the retained [first execution prompt](attempt-1-prompt.txt) explicitly requires the service to return `java.util.Map<String,Object>` containing `shortCode` and `expired`, followed by “Controller delegates.” This is a stronger instruction. The [second prompt](continuation-2-prompt.txt) also requests a map. The [final prompt](../brownfield-final-attempt/prompt.txt) retains delegation and response fields without explicitly repeating the service return type. Omission from that later prompt is not evidence of a human-approved architectural change.

The actual service returns a `ShortUrl` entity under `@Transactional(readOnly = true)`. The controller constructs the map and calls `shortUrl.isExpired(Instant.now())`. Thus service-side response construction and expiration evaluation are **not implemented**. The reviewed high-level artifact alone permits this separation, but the stricter recorded execution instruction does not. This report leaves the discrepancy unresolved rather than converting a green build into an architectural waiver. No source relocation or design change was made during this review.

## Validation and source provenance

The original proposal remains [the verbatim live model response](../brownfield-final-attempt/response.txt), hash-bound to the historical attempt and transaction. The [original diff](../brownfield-offline-validation/original-proposal.diff), [exact import correction](../brownfield-offline-validation/compilation-correction.diff), and [corrected diff](../brownfield-offline-validation/corrected-proposal.diff) distinguish AI output from the assistant-authored correction. [Corrected source snapshots](corrected-source/url-shortener/src/main/java/url_shortener/) and the [unchanged trusted test](corrected-source/url-shortener/src/test/java/url_shortener/UrlStatusIntegrationTest.java) are retained here; SHA-256 values are in [the machine-readable review](review.json).

The [failed Maven log](../brownfield-offline-validation/original/maven.log) reproduces the single missing-import diagnostic. The [corrected Maven log](../brownfield-offline-validation/corrected/maven.log), [case results](../brownfield-offline-validation/corrected/result.json), and [Surefire XML](../brownfield-offline-validation/corrected/surefire-reports/) record genuine Java 21 execution. XML was independently re-read for this final review: all 11 cases passed without skips or errors. Maven was not rerun for this documentation-only review.

Status tests establish missing-code HTTP 404, a read-only service annotation, expired/future/missing expiration states, unchanged clicks after status reads, and normal redirect increments. Original tests retain other HTTP/expiration behavior. These checks do not establish every architectural property, transaction-isolation behavior under concurrency, or production readiness.

## Workflow and transaction provenance

The original workflow `e4e0d578-28db-40a3-944c-32a60be1722c` is still terminal. Attempts 1 and 2 are consumed interruptions; attempt 3 completed model generation but failed engineering compilation. [The source and additive-test journals](../brownfield-final-attempt/journals.json) remain rolled back. [The terminal result](../brownfield-final-attempt/result.json), [ledger](../brownfield-final-attempt/attempts.json), and [audit](../brownfield-final-attempt/audit.json) are not superseded by this report.

The offline copy was created under the user's separate diagnostic authorization. It has no new live workflow execution receipt or successful source transaction: its provenance is the explicit disposable-copy procedure, original journal linkage, exact correction diff, source hashes, and real Maven evidence. It is not autonomous recovery, a fourth model attempt, or retrospective approval of the failed execution.

Final read-only verification matched every historical execution file against the prior preservation inventory, including databases, WAL/SHM files, approvals, ledger, source, journals, and logs. Existing evidence files were not edited. No Ollama operation or model call occurred during this finalization. The source snapshots in this report are assessment artifacts, not production-checkout modifications.

The assessment can claim tested offline-assisted functionality and successful preservation/rollback of the failed live attempt. It cannot claim a successful autonomous brownfield scenario, full compliance with the stricter service-side instruction, or a human-approved result. Resolving that architectural discrepancy and authorizing any further execution remain separate decisions.
