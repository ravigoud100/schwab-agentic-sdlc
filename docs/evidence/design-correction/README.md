# Controlled design correction — October 16, 2026

The user identified the stale narrative risk and requested a design correction without changing requirements. The workflow resumed the existing design-review interrupt with `request_changes` and explicit feedback. Real local `qwen2.5-coder:latest` generated version 2 in one call. The obsolete “decision not approved” risk is removed, the selected custom UTC expiration decision remains explicit, and all eight approved obligations remain covered. Entry and exit gates passed again. No artifact was hand-edited.

Review [the original artifact](original-design.json), [requested feedback](feedback.json), [model prompt](attempt-1-prompt.txt), [raw response](attempt-1-response.json), [revised artifact](revised-design.json), [state summary](result.json), and [correction audit](run-events.json).

The original artifact/hash is retained in `DESIGN_CHANGES_REQUESTED` SQLite audit details and version history. Requirement version 1, its decision hash, and the approved obligation-contract hash are unchanged. The corrected artifact has a different version-bound design hash. Old checkpoints retain compatibility with the earlier unversioned hash format; new artifacts bind their explicit design version.

The run forks the prior read-only design checkpoint database and resumes the same thread ID on that branch. Databases and workspace references remain under `/private/tmp/schwab-design-correction/`. No Java files changed. The [pre-approval state](result.json) records that planning remained blocked at design review. The user subsequently reviewed and explicitly approved version 2. The workflow resumed on the same checkpoint branch and now waits at engineering-plan approval; no implementation was approved or executed.

The focused regression selection passes **189 tests**: the previous 180 plus nine design-correction tests. New tests cover full graph correction, old artifact persistence in SQLite, requirement/contract preservation, version/hash changes, stale approval rejection, corrected-design handoff to planning after approval, invalid feedback re-interruption, unchanged-artifact retry exhaustion, CLI correction/rejection, and source preservation. An explicit new human request starts a bounded regeneration budget; the model cannot initiate unlimited review cycles.

The validator checks structure, declared coverage, and selected typed consistency rules. It does not prove that arbitrary narrative feedback has been addressed. The revised narrative was inspected directly for this bounded case; no general natural-language contradiction detector is claimed. HTTP 400 remains a proposed design detail rather than a previously imposed requirement.

## Approved-design handoff

After explicit human approval, one real planning call generated [the engineering plan](plan.json), which passed semantic coverage validation and covers all eight obligations. [Final planning state](planning-result.json) binds `approved_design_hash` and `plan_design_hash` to version 2 hash `4b956c37bad924eb54b1e563ebae1f4f49e5e0f64eef1bd4d108d85f3ea572cf`. The requirement and contract hashes are unchanged. The original design hash is `fc8e91078a167e302d60568f6010ce82719acb464698f2393df35810fe1bdbda` and cannot authorize the revision.

[Complete correction-to-planning audit](complete-run-events.json) records the change request, repeated gates, design approval, semantic plan acceptance, and plan creation. [Verification summary](summary.json) records 18 graph checkpoints, the same thread ID, eight obligations in both artifacts, and zero Java changes. The earlier generic-proposal entry retained in `semantic_history` predates this correction and is not a new planning failure; this handoff used one planning call. Later bookkeeping resets the new plan's retry budget and omits stale error context after design changes; it does not change the retained model responses.

Reproduce generation with `python scripts/design_correction_demo.py NEW_DIRECTORY --source DESIGN_REVIEW_CHECKPOINT_DIRECTORY`. After a human reviews `revised-design.json`, resume the design decision with `python scripts/design_correction_demo.py NEW_DIRECTORY --decision approve` (or `reject`). The runner stops at engineering-plan review and never approves source edits. General structured narrative-consistency checking remains a separate improvement.

## Engineering plan review

[The engineering review](PLAN_REVIEW.md) recommends plan changes before implementation: generation needs authoritative approved context, deterministic clock testing needs a concrete seam, existing tests need enforced protection, and every proposed source edit needs justification. HTTP 400 was explicitly accepted in the design-v2 approval and matches existing behavior. The workflow remains at plan approval; no source authorization or execution was submitted.
