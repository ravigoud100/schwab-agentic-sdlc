# Assessment scenario review packets — October 16, 2026

This packet prepares the three required assessment scenarios without treating the preparation itself as an implementation approval. Every proposal is currently `AWAITING_PLAN_APPROVAL`; no source change, model call, Maven run against a proposed change, release, merge, or deployment has occurred for these scenarios.

The review packets are [greenfield](greenfield-proposal.json), [brownfield](brownfield-proposal.json), and [ambiguous](ambiguous-proposal.json). [The summary](scenario-summary.json) binds their hashes and records three SQLite `SCENARIO_REVIEW_REQUESTED` events in the locally generated `audit.db`.

## Greenfield

The proposal creates exactly one new production component, `UrlHealthComponent.java`, reporting a deterministic ready status. The transaction engine now supports a prospectively declared new production Java file only when its exact path is in the reviewed scope. It verifies that the path was absent at the approved baseline, applies it through the workspace transaction journal, and deletes it only when its expected hash is still present during rollback. Arbitrary new files, configuration, dependencies, and existing-test changes remain denied.

The component has not been generated or written. Approval must cover the proposed requirements, architecture, exact new-file path, and validation plan before a model is asked to propose the file contents. This is a capability extension, not greenfield acceptance evidence yet.

## Brownfield

The proposal is a read-only URL status enhancement with an impact boundary of `UrlService.java` and `UrlController.java`. Its tasks are intentionally ordered: service behavior first, then the controller. A combined source transaction must validate both files together. A validation failure must restore both; a material recovery change needs a new approved plan receipt. No earlier status-API plan, scope, or transaction has been adopted as authorization for this fresh scenario.

The status implementation has not been generated or written. The proposal records the intended 404/read-only/click-count compatibility assertions but does not claim they have passed.

## Ambiguous requirement

The initial requirement deliberately leaves expiration selection unspecified. Its clarification offers two valid, incompatible decisions:

| Selected decision | Required planning and behavior consequence |
| --- | --- |
| Caller-provided UTC timestamp | Optional timestamp input; a fixed UTC clock rejects past/equal values; missing expiration remains supported. |
| Fixed 24-hour TTL | No caller expiration input; creation computes exactly 24 hours; missing input is irrelevant. |

The machine-checked catalog rejects duplicate plan effects, so an answer cannot be recorded while retaining a generic shared plan. This packet has not selected an answer; a human clarification interrupt is still required. Therefore no validated behavior is claimed for either branch.

## Verification completed before review

The complete Python suite passed **463 tests** after adding five scenario/catalog and prospective-new-file lifecycle tests; no existing test was removed or weakened. Those tests verify that a greenfield path needs explicit approval, a prospective new source is journaled, and failed validation compensates by deleting only the created file.

An unchanged local Java 21 offline Maven baseline ran `./mvnw -o -Dmaven.repo.local=/private/tmp/schwab-test-generation/maven-cache clean test` on October 16, 2026: **8 tests passed with zero failures, errors, or skips**. This validates the current checkout only. It is not validation of any unapproved scenario source change.

## Remaining steps and limits

Each scenario needs a separate human decision before source execution. The greenfield and brownfield runs need an isolated workspace, an explicit plan authorization receipt, pre-write policy evaluation, real Maven validation, documentation backed by resulting evidence, and final release-readiness evaluation. The ambiguous run must first save the selected answer and prove that the resulting plan and tests reflect it.

No live AI generation, synthetic approval, imported checkpoint, organizational sign-off, release authorization, merge, or deployment is represented as a completed scenario. The new-file path remains limited to existing source directories and does not support arbitrary package directories, dependencies, configuration, build changes, or new test files outside the separately authorized test stage.
