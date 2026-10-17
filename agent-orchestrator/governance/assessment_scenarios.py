"""Bounded, approval-first assessment scenario definitions.

The catalog makes the three assessment claims reviewable before an executor can
write source.  It intentionally does not manufacture a human plan approval or
turn a deterministic fixture into a live-model result.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from governance.audit_logger import log_event
from governance.file_transaction import apply_transaction, finish_transaction


class ScenarioError(ValueError):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


GREENFIELD = "url-shortener/src/main/java/url_shortener/UrlHealthComponent.java"
SERVICE = "url-shortener/src/main/java/url_shortener/UrlService.java"
CONTROLLER = "url-shortener/src/main/java/url_shortener/UrlController.java"


def catalog() -> dict:
    """Machine-readable scenario contracts; all source scope is exact."""
    return {
        "greenfield": {
            "classification": "greenfield",
            "requirement": "Add a new URL health component that reports a stable ready status.",
            "architecture": {
                "component": GREENFIELD,
                "contract": "UrlHealthComponent.status() returns the literal ready status.",
                "compatibility": "A Spring component with no external I/O; no controller, dependency, configuration or existing test change.",
            },
            "plan": {"tasks": [{"task_id": "add-url-health-component", "execution_type": "implementation",
                "description": "Create only UrlHealthComponent as an injectable Spring component with a deterministic ready status.",
                "dependencies": [], "allowed_files": [], "allowed_new_files": [GREENFIELD]}]},
            "validation": {"command": "maven clean test", "assertions": ["Spring context starts", "existing tests remain executable"]},
            "documentation": {"required": ["new component contract", "source diff", "Maven evidence", "known limits"]},
        },
        "brownfield": {
            "classification": "brownfield",
            "requirement": "Add a read-only URL status response without changing redirect click counting.",
            "architecture": {"components": [SERVICE, CONTROLLER],
                "contract": "GET status reads a URL and reports short code and expiration state without incrementing clicks.",
                "compatibility": "Unknown codes remain 404; redirect and analytics behavior remain unchanged."},
            "plan": {"tasks": [
                {"task_id": "status-service", "execution_type": "implementation", "dependencies": [],
                 "description": "Add a read-only status method with no click increment.", "allowed_files": [SERVICE], "allowed_new_files": []},
                {"task_id": "status-controller", "execution_type": "implementation", "dependencies": ["status-service"],
                 "description": "Expose the approved status response and preserve 404 behavior.", "allowed_files": [CONTROLLER], "allowed_new_files": []},
            ]},
            "recovery": "A combined Maven failure rolls back both files; a material revision needs a new plan authorization.",
            "validation": {"command": "maven clean test", "assertions": ["404 preserved", "status is read-only", "redirect clicks unchanged"]},
        },
        "ambiguous": {
            "classification": "brownfield",
            "requirement": "Support expiration for newly created short URLs.",
            "ambiguities": [{"id": "expiration-mode", "question": "How is expiration selected?",
                "options": ["Caller-provided UTC timestamp", "Fixed 24-hour TTL"], "default_assumption": None}],
            "answers": {
                "Caller-provided UTC timestamp": {"selected": "Caller-provided UTC timestamp",
                    "plan_effect": "Accept optional Instant input, reject at-or-before fixed UTC now, retain missing expiration.",
                    "behavior": ["future accepted", "past/equal rejected", "missing accepted"]},
                "Fixed 24-hour TTL": {"selected": "Fixed 24-hour TTL",
                    "plan_effect": "Do not expose caller expiration input; calculate creation time plus exactly 24 hours.",
                    "behavior": ["caller timestamp ignored", "expires at fixed TTL", "missing input irrelevant"]},
            },
        },
    }


def validate_catalog(data: dict | None = None) -> dict:
    data = catalog() if data is None else data
    if set(data) != {"greenfield", "brownfield", "ambiguous"}:
        raise ScenarioError("Assessment catalog requires exactly greenfield, brownfield and ambiguous scenarios")
    green = data["greenfield"]["plan"]["tasks"][0]
    if green["allowed_files"] or green["allowed_new_files"] != [GREENFIELD]:
        raise ScenarioError("Greenfield scope must be one explicit new production file")
    tasks = data["brownfield"]["plan"]["tasks"]
    if len(tasks) != 2 or {p for task in tasks for p in task["allowed_files"]} != {SERVICE, CONTROLLER}:
        raise ScenarioError("Brownfield scope must be exactly the reviewed service/controller files")
    answers = data["ambiguous"]["answers"]
    if len(answers) != 2 or len({v["plan_effect"] for v in answers.values()}) != 2:
        raise ScenarioError("Clarification options must materially change the plan")
    return data


def prepare(directory: Path, workflow_id: str = "assessment-scenarios") -> dict:
    """Write only review artifacts and an audit record; no source work is authorized."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    data = validate_catalog()
    for name, scenario in data.items():
        (directory / f"{name}-proposal.json").write_text(json.dumps(scenario, indent=2) + "\n")
        log_event(workflow_id, "assessment_scenarios", "SCENARIO_REVIEW_REQUESTED", {
            "scenario": name, "classification": scenario["classification"],
            "proposal_hash": digest(scenario), "decision": "pending_human_approval",
        })
    report = {"workflow_id": workflow_id, "catalog_hash": digest(data),
              "status": "AWAITING_SCENARIO_PLAN_APPROVALS",
              "live_generation": False,
              "source_writes": 0,
              "release_authorized": False,
              "scenarios": {name: {"proposal_hash": digest(value), "status": "AWAITING_PLAN_APPROVAL"}
                            for name, value in data.items()}}
    (directory / "scenario-summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def apply_approved_greenfield(workspace: Path, workflow_id: str, source: bytes, *, approved: bool) -> dict:
    """Execute an already reviewed greenfield source only after caller approval.

    The caller owns the plan-review interrupt.  This routine is deliberately
    unusable with a false/missing approval and only grants a new-file transaction,
    never test/release approval.
    """
    if approved is not True:
        raise PermissionError("Explicit greenfield plan approval is required")
    result = apply_transaction({GREENFIELD: source}, {GREENFIELD: None}, Path(workspace), [], workflow_id,
                               allowed_new_files=[GREENFIELD])
    log_event(workflow_id, "assessment_scenarios", "GREENFIELD_SOURCE_APPLIED", {
        "transaction_id": result["transaction_id"], "path": GREENFIELD,
        "source_hash": hashlib.sha256(source).hexdigest(), "approval": "explicit"})
    return result


def finish_greenfield(transaction: dict, workspace: Path, *, passed: bool) -> None:
    finish_transaction(transaction["transaction_id"], Path(workspace), [], passed=passed,
                       allowed_new_files=[GREENFIELD])
