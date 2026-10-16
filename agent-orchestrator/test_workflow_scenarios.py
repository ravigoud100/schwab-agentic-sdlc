import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from main import (
    EngineeringState,
    approved_node,
    failure_node,
    implementation_node,
    planning_agent,
    quality_gate,
    replanning_node,
    rejected_node,
    repair_executor_node,
    success_node,
    validation_node,
)


RELATIVE_JAVA_FILE = "url-shortener/src/main/java/url_shortener/ShortUrl.java"


def _task_state(approval="approve"):
    return {
        "workspace": "/tmp/test-workflow",
        "workflow_id": "scenario-test",
        "requirement": "Add URL expiration",
        "approval": approval,
        "plan": "Implement expiration.",
        "plan_version": 1,
        "replan_history": [],
        "validation_result": {},
        "retry_count": 0,
        "tasks": [
            {
                "task_id": "implementation",
                "description": "Implement the approved change",
                "dependencies": [],
                "status": "READY",
                "allowed_files": [RELATIVE_JAVA_FILE],
            }
        ],
        "active_task_ids": ["implementation"],
        "task_plan_valid": True,
        "implementation_changed": False,
        "repair_applied": False,
        "repair_rolled_back": False,
        "repair_rollback_blocked": False,
    }


def test_successful_execution_reaches_completion():
    state = _task_state()
    with TemporaryDirectory() as directory:
        modified_file = Path(directory) / "ShortUrl.java"
        modified_file.write_text("updated", encoding="utf-8")
        checkpoint = {"path": RELATIVE_JAVA_FILE, "original_hash": "old"}
        with patch("main.create_checkpoint", return_value=checkpoint), patch(
            "main.implement_expiration", return_value=modified_file
        ), patch("main.file_hash", return_value="new"), patch("main.log_event"):
            implementation = implementation_node(state)

        passed = {
            **state,
            **implementation,
        }
        with patch(
            "main.validate_implementation",
            return_value={
                "passed": True,
                "exit_code": 0,
                "duration_seconds": 0.1,
                "output": "BUILD SUCCESS",
            },
        ), patch("main.log_event"):
            validated = validation_node(passed)

        completed = {**passed, **validated}
        assert quality_gate(completed) == "passed"
        assert success_node(completed)["status"] == "AWAITING_FINAL_HUMAN_REVIEW"
        assert completed["tasks"][0]["status"] == "COMPLETED"


def test_rejected_plan_stops_without_implementation():
    state = _task_state(approval="reject")
    with patch("main.log_event"), patch("main.implement_expiration") as implement:
        assert rejected_node(state)["status"] == "STOPPED"
        result = implementation_node(state)
    implement.assert_not_called()
    assert result["status"] == "STOPPED"


def test_validation_failure_triggers_replanning():
    state = _task_state()
    state["active_task_ids"] = []
    failed = {
        "passed": False,
        "exit_code": 1,
        "duration_seconds": 0.2,
        "output": "Compilation failed: missing expiresAt accessor",
    }
    replan_response = SimpleNamespace(
        content=json.dumps(
            {
                "revised_plan": "Inspect the accessor, then apply a minimal approved repair.",
                "change_summary": "Added an accessor inspection before repair.",
            }
        )
    )
    with patch("main.validate_implementation", return_value=failed), patch(
        "main.model", SimpleNamespace(invoke=Mock(return_value=replan_response))
    ), patch("main.log_event"):
        validation = validation_node(state)
        replanned = replanning_node({**state, **validation})

    assert quality_gate({**state, **validation}) == "replan"
    assert replanned["replan_meaningful_change"] is True
    assert replanned["plan_version"] == 2


def test_approved_repair_is_restricted_to_authorized_change():
    state = {
        **_task_state(),
        "repair_approval": "approve",
        "repair_change": {
            "path": RELATIVE_JAVA_FILE,
            "old_text": "old",
            "new_text": "new",
        },
    }
    execution = {
        "path": RELATIVE_JAVA_FILE,
        "checkpoint": {"path": RELATIVE_JAVA_FILE},
        "expected_current_hash": "new",
        "changed": True,
    }
    with patch("main.apply_approved_repair", return_value=execution) as apply, patch(
        "main.log_event"
    ):
        result = repair_executor_node(state)
    apply.assert_called_once_with(state["repair_change"], Path(state["workspace"]))
    assert result["repair_applied"] is True


def test_failed_repair_rolls_back_safely():
    state = {
        **_task_state(),
        "repair_applied": True,
        "repair_checkpoint": {"path": RELATIVE_JAVA_FILE},
        "repair_expected_current_hash": "new",
    }
    failed = {
        "passed": False,
        "exit_code": 1,
        "duration_seconds": 0.1,
        "output": "Repair validation failed",
    }
    with patch("main.validate_implementation", return_value=failed), patch(
        "main.rollback", return_value=True
    ) as rollback, patch("main.log_event"):
        result = validation_node(state)
    rollback.assert_called_once_with(state["repair_checkpoint"], "new", Path(state["workspace"]))
    assert result["repair_rolled_back"] is True
    assert result["repair_applied"] is False


def test_retry_exhaustion_stops_without_looping():
    state = {
        "validation_result": {"passed": False},
        "retry_count": 2,
        "replan_attempted": True,
        "replan_meaningful_change": True,
        "repair_rollback_blocked": False,
    }
    assert quality_gate(state) == "failed"
    assert failure_node(
        {
            "workspace": "/tmp/test-workflow",
        "workflow_id": "exhausted",
            "implementation_changed": False,
            **state,
        }
    )["status"] == "STOPPED_VALIDATION_FAILED"
