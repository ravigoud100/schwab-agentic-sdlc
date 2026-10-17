# Security, compliance and operational policy gates — October 16, 2026

The new declarative policy engine rejects supported classes of unsafe proposed Java changes before source writes and performs a fresh policy/preservation evaluation before readiness is reported. **Technical controls pass on the retained integrated implementation; release readiness remains `BLOCKED`.** No organizational security, compliance, or operational sign-off was supplied or inferred. No historical workflow was resumed, and no release, merge or deployment occurred.

Review [the policy](policy.json), [seeded outcomes](scenarios.json), [SQLite policy events](new-policy-events.json), [retrospective readiness](readiness.json), [historical integrity verification](historical-integrity.json), and [genuine Maven results](maven/result.json). The policy SHA-256 is `44fbe715aa936848519ca742f5682c21e2c63962ad89e3c9f9dff474667c645d` (canonical JSON digest).

## Enforced controls

| Policy | Enforcement |
| --- | --- |
| `SEC-SECRETS` | Denies recognized hardcoded Java credential/token literals, AWS access-key patterns, and private-key headers. Configuration-loaded values remain permitted. |
| `SEC-PROCESS` | Denies recognized Runtime execution, unsupported ProcessBuilder commands, reflective loading and command/environment/redirection changes. Only the exact constant `/usr/bin/true` ProcessBuilder construction is eligible for additional review. |
| `SEC-SQL` | Denies recognized SQL string concatenation, formatted SQL and SQL StringBuilder/StringBuffer construction. Parameterized SQL is permitted. |
| `COM-PII-LOG` | Denies recognized sensitive identifiers, email literals and SSN literals in common logger/System print calls. |
| `SEC-DEPENDENCY-ALLOWLIST` | Checks imports and fully qualified external class references against the declared package-prefix allowlist. Build/dependency-file modification is unsupported and denied. |
| `GOV-PROTECTED-CONFIG` | Denies non-Java, non-source, noncanonical and traversal proposal paths, including Maven and application configuration. |
| `TEST-PRESERVE-ORIGINAL` | Existing Java tests are byte-immutable. Changing assertions, deleting content or otherwise editing existing tests is unsupported. |
| `TEST-DISABLE` | Denies recognized disabling/skip annotations, assumptions/abort calls, trivial constant assertions and Maven skip/exclusion settings. |
| `TEST-UNAUTHORIZED-CREATION` | Additive tests require an exact trusted path from the already authorized vetted test stage. No arbitrary new test scope is inferred. |
| `GOV-OBFUSCATION`, `GOV-UNSCANNABLE` | Denies Java Unicode escapes under this conservative profile, empty/undecodable source and unsupported policy configurations. |

The shared transaction manager evaluates the entire proposal under its existing workspace lock before file checkpoints, staging or source replacement. Parallel workers check read-only proposals, and the joined transaction checks the complete set again. Both additive test-publication paths are checked before file publication. Legacy implementation and repair forward-write paths are also guarded; repair retains the actual workflow ID through a context-local scope without changing existing mock interfaces. Source snapshots are rechecked after policy evaluation, and legacy repair now rejects internal symlink aliases as well as external escapes. Hash-protected rollback restores original bytes without applying forward-generation policy to the restoration itself.

Pre-release evaluation first requires the existing independent evidence/lineage verifier, then reloads the protected baseline, rechecks the complete project inventory, and scans current Java bytes. It does not reuse a saved technical-pass flag. Readiness separately records static controls, protected configuration/tests, and verified passing validation evidence. Human security, compliance and operational sign-offs remain false, along with the existing aggregate sign-off blocker. Deployment authorization remains false.

## Additional approval and audit authority

Unsupported risks are unconditional denials. The single supported high-risk profile requires a **separate LangGraph `POLICY_EXCEPTION_APPROVAL` interrupt** from `governance.policy_engine.build_review_workflow()`. It reviews frozen original/proposed bytes and grants no file, engineering-plan, test, release or deployment authorization. There is no automatic approval. Existing callers can provide the resulting `policy_approvals` receipt only when their existing plan/file authorization permits the exact write. The default generation/recovery path stops on a policy-approval requirement; automatic generic policy-review/retry integration is not claimed. Parallel combined scopes need their own matching approval and cannot adopt a per-task or sequential receipt.

Receipts bind workflow, canonical workspace, original/proposed file hashes, additive-test scope, policy hash, phase and findings through the evidence digest. Changing source, originals, workspace, workflow, policy, phase, receipt, SQLite evidence body or decision prevents reuse. The checker recomputes the evidence and compares the entire latest approval audit payload, not a caller-supplied pass flag. A later explicit rejection invalidates an earlier approval for the same exception. Pre-write approval cannot stand in for release-phase review.

SQLite records policy identifiers, checked controls, file-hash scope, decision, finding line numbers, evidence hashes and approval references. Findings contain hashes rather than source snippets or secret values. [Synthetic exception-review evidence](synthetic-exception-review.json) comes from a deterministic pytest human-decision fixture, not a real human approval or organizational sign-off. No live high-risk exception was approved in this milestone.

## Observed outcomes

The retained integrated engineering bundle `8f270e4214621ce73f8031dec89d475ad1cc64dd4671c5f317d498cf682654f5` was independently reverified and evaluated using a **copied** SQLite audit database. New policy/readiness events exist only in that copy. The original databases and approval decisions remain byte-identical. This is retrospective technical evaluation, not continuation or replacement of the earlier blocked final review.

Eight retained transaction fixtures show six denied seeded risks, one reviewable process capability stopped pending approval, and one legitimate source change applied then rolled back for fixture cleanup. Denied fixtures made zero source writes. These are deterministic enforcement fixtures, not live AI generation or newly approved product plans.

The [first scan](initial-false-positive/readiness.json) failed because the initial package allowlist/matching rejected existing JPA wildcard and Hibernate URL-annotation imports. Its [findings](initial-false-positive/audit.json) are preserved. Wildcard package matching was corrected and the existing Hibernate constraint package was explicitly declared; regression coverage preserves that legitimate use. No Java, requirements, tests or build files were changed to make the scan pass.

A fresh copy of the previously approved integrated implementation ran genuine Java 21 offline Maven `clean test`: **12 tests passed, zero failures/errors/skips**. See [Maven log](maven/maven.log) and XML reports. Mockito JVM attachment required the already established execution permission outside the sandbox; no JVM flags or build files were changed. Final policy refinements were re-evaluated separately on the same unchanged retained Java bytes. This milestone introduces no new Java feature implementation.

The complete Python suite passes **458 tests**, preserving all prior 409 and adding 49 policy cases. They cover all seeded controls, legitimate imports/parameterized SQL/configuration-loaded secrets, additive test authorization, explicit exception approval/rejection, changed evidence and policy versions, later rejection, malformed policy, existing workflow/mock compatibility, legacy repair lineage and external changes, and fresh release scans rejecting secrets/test/build tampering despite an earlier saved technical pass. Technical-pass tests still require readiness to remain blocked without human sign-offs.

## Reproduction, limits and remaining blockers

From `agent-orchestrator/`, `../.venv/bin/python scripts/policy_gate_demo.py NEW_DIRECTORY --retained-workflow /private/tmp/schwab-integrated-sdlc-fixed` creates seeded transaction fixtures and retrospective readiness evidence in a new audit copy. It does not approve exceptions or run Maven. The retained Maven copy is `/private/tmp/schwab-policy-gates-verified/`; the final policy/evidence run is `/private/tmp/schwab-policy-evidence/`. Original failed-scan evidence remains under `/private/tmp/schwab-policy-gates/`. Export hashes bind the portable report files.

These are conservative lexical controls over supported Java proposals, not AST/data-flow analysis, exploit detection, DLP, dependency-version/CVE/license/SBOM analysis, or certification of regulatory compliance. Package allowlisting does not establish that every API in an allowed package is safe. Aliasing, runtime-built strings, indirect logging, unrecognized secrets/PII and other obfuscation can escape pattern detection; legitimate comments/strings can produce false positives. Configuration changes and existing-test edits are denied rather than automatically reviewed/remediated. Existing configuration is protected against modification; this is not a general historical configuration-secret scanner.

Policy files and local SQLite authority are trusted administrative inputs. Receipts are not signed and do not defend against a privileged administrator fabricating matching state and audit rows. Policy logs avoid plaintext findings, but the broader existing model/source evidence retention is not a redaction or secrets-management system. The legacy direct-write helpers retain their historical checkpoint mechanism; this does not upgrade them to simultaneous filesystem transactions or eliminate unrelated-writer/storage-failure risks.

Operational evidence here means preserved inventory, lineage and genuine passing test validation. It does not establish monitoring, incident response, production access controls, backup/restore exercises, capacity, external security review or actual organizational sign-off. Those reviews and attestations, plus release/deployment authorization, remain outstanding. No readiness pass, merge, release or deployment is inferred from technical checks.
