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


def test_mock_verification_plan_runs_validation_without_source_changes(tmp_path):
    from llm.provider import MockProvider
    from main import task_scheduler_node, task_scheduler_router, verification_node

    target = tmp_path / RELATIVE_JAVA_FILE
    target.parent.mkdir(parents=True)
    # Tokens alone are deliberately insufficient evidence of correctness.
    original = b"// expiresAt getExpiresAt isExpired\n"
    target.write_bytes(original)
    state = {**_task_state(), "workspace": str(tmp_path), "analysis": "", "codebase_analysis": ""}
    with patch("main.model", MockProvider("url-expiration")):
        state.update(planning_agent(state))
    from main import human_approval
    with patch("main.interrupt", return_value="approve"):
        state.update(human_approval(state))
    with patch("main.log_event"), patch("main.implement_expiration") as implement, patch(
        "main.create_checkpoint"
    ) as checkpoint, patch("main.apply_approved_repair") as repair:
        state.update(task_scheduler_node(state))
        assert task_scheduler_router(state) == "verification"
        state.update(verification_node(state))
        assert state["tasks"][0]["status"] == "RUNNING"
        for passed in (True, False):
            result = {"passed": passed, "exit_code": 0 if passed else 1,
                      "duration_seconds": 0.1, "output": "validation result"}
            with patch("main.validate_implementation", return_value=result) as validate:
                validated = {**state, **validation_node(state)}
            validate.assert_called_once_with(tmp_path)
            assert validated["tasks"][0]["status"] == ("COMPLETED" if passed else "FAILED")
            assert quality_gate(validated) == ("passed" if passed else "failed")
            assert validated["source_files_changed"] == 0
            if passed:
                assert success_node(validated)["status"] == "AWAITING_FINAL_HUMAN_REVIEW"
            if not passed:
                assert failure_node(validated)["status"] == "STOPPED_VALIDATION_FAILED"
        implement.assert_not_called()
        checkpoint.assert_not_called()
        repair.assert_not_called()
    assert target.read_bytes() == original


def test_verification_requires_approval():
    import pytest
    from main import verification_node

    state = _task_state(approval="reject")
    with pytest.raises(PermissionError):
        verification_node(state)


def test_validation_only_detects_source_changes(tmp_path):
    from main import verification_node

    target = tmp_path / RELATIVE_JAVA_FILE
    target.parent.mkdir(parents=True)
    target.write_text("original")
    state = {**_task_state(), "workspace": str(tmp_path), "active_task_ids": ["verify-expiration"]}
    state["tasks"][0]["task_id"] = "verify-expiration"
    state["tasks"][0]["execution_type"] = "validate_only"
    from main import _task_definition_hash
    state["approved_task_hash"] = _task_definition_hash(state["tasks"])
    with patch("main.log_event"):
        state.update(verification_node(state))

        def changed_during_validation(workspace):
            target.write_text("unexpected build modification")
            return {"passed": True, "exit_code": 0, "duration_seconds": 0.1, "output": "BUILD SUCCESS"}

        with patch("main.validate_implementation", side_effect=changed_during_validation):
            result = validation_node(state)
    assert result["source_files_changed"] == 1
    assert result["validation_result"]["passed"] is False
    assert quality_gate({**state, **result}) == "failed"


def test_validation_only_graph_requires_approval_then_reaches_final_review(tmp_path):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from llm.provider import MockProvider
    from main import workflow

    target = tmp_path / RELATIVE_JAVA_FILE
    target.parent.mkdir(parents=True)
    original = b"package url_shortener; public class ShortUrl {}\n"
    target.write_bytes(original)
    state = {**_task_state(), "workspace": str(tmp_path), "approval": "", "tasks": [],
             "active_task_ids": [], "task_plan_valid": False}
    model = MockProvider("url-expiration")
    app = workflow.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "validation-only-graph"}}
    passed = {"passed": True, "exit_code": 0, "duration_seconds": 0.1, "output": "BUILD SUCCESS"}
    with patch("main.model", model), patch("agents.codebase_agent.get_model", return_value=model), patch(
        "main.validate_implementation", return_value=passed
    ) as validate, patch("main.implement_expiration") as implement, patch(
        "main.create_checkpoint"
    ) as checkpoint, patch("main.apply_approved_repair") as repair, patch("main.log_event"):
        paused = app.invoke(state, config=config)
        assert "__interrupt__" in paused
        validate.assert_not_called()
        assert target.read_bytes() == original
        events = list(app.stream(Command(resume="approve"), config=config, stream_mode="updates"))
        nodes = [node for event in events for node in event]
        assert nodes == ["approval", "approved", "task_scheduler", "verification", "validation", "success"]
        result = app.get_state(config).values
        validate.assert_called_once_with(tmp_path)
        implement.assert_not_called()
        checkpoint.assert_not_called()
        repair.assert_not_called()
    assert result["status"] == "AWAITING_FINAL_HUMAN_REVIEW"
    assert result["tasks"][0]["status"] == "COMPLETED"
    assert result["source_files_changed"] == 0
    assert target.read_bytes() == original
