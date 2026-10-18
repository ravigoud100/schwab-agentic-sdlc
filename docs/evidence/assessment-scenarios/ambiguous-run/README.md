# Ambiguous scenario: clarification and planning run evidence

**Status: `PLAN_CREATED` — awaiting engineering plan approval**
Workflow `06a48ae8-a82f-44e2-8232-74a7383f900c`

This directory is a complete archive of `/private/tmp/schwab-assessment-ambiguous/`
copied verbatim on 2026-10-16. No file was edited during or after the copy.

---

## SQLite lineage (audit.db — 4 events)

| # | Event | Agent | Timestamp (UTC) | Key details |
|---|---|---|---|---|
| 1 | `WORKFLOW_STARTED` | `clarification_demo` | 2026-10-17T01:59:27 | Ambiguous requirement presented |
| 2 | `REQUIREMENTS_VALIDATED` | `requirements_agent` | 2026-10-17T01:59:36 | Ambiguity ID: `A1`; req hash `23bf75a1…` |
| 3 | `CLARIFICATION_APPROVED` | `human_clarification` | 2026-10-17T01:59:44 | **A1 = "Custom expiration timestamp"**; req hash `1de7e944…` |
| 4 | `PLAN_CREATED` | `planning_agent` | 2026-10-17T02:00:20 | plan hash `e3726f8d…`; task hash `6effd4cb…` |

---

## Decision record

| Field | Value |
|---|---|
| Ambiguity ID | `A1` |
| Question | "How is expiration selected?" |
| **Human selection** | **"Custom expiration timestamp"** |
| Not selected | "Fixed 24-hour TTL" |
| Clarification req hash | `1de7e94476528dfd37493b75a48cc91b60b085cf152d34b710f7387139705b2d` |
| Plan hash | `e3726f8d5c537ce485dbd7c9f249e7668f8b4158749f1784974934e2b7818298` |

---

## Current state

- No source writes occurred in this workflow.
- The workflow halted at the `approval` interrupt (`pending_approvals`) awaiting engineering plan approval.
- The plan stored in `plan.json` describes the general task; a scenario-specific plan for
  exactly the "Caller-provided UTC timestamp" branch is required before implementation.
- This archived evidence is the permanent SQLite record of the human clarification decision.
