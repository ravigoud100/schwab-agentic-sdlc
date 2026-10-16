"""Regression coverage through the graph and real validation-agent boundary."""
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from llm.provider import MockProvider
from main import (
    IMPLEMENTATION_RELATIVE_PATH,
    implementation_node,
    task_scheduler_router,
    workflow,
)
from agents.validation_agent import validate_implementation


@pytest.mark.parametrize("decision,exit_code,expected_status", [
    ("approve", 0, "AWAITING_FINAL_HUMAN_REVIEW"),
    ("approve", 1, "STOPPED_VALIDATION_FAILED"),
    ("reject", 0, "STOPPED"),
])
def test_validation_only_graph_executes_maven_only_after_approval(
    tmp_path, decision, exit_code, expected_status,
):
    target = tmp_path / IMPLEMENTATION_RELATIVE_PATH
    target.parent.mkdir(parents=True)
    original = b"package url_shortener; public class ShortUrl {}\n"
    target.write_bytes(original)
    provider = MockProvider("url-expiration")
    app = workflow.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "validation-regression"}}
    state = {
        "workflow_id": "validation-regression", "workspace": str(tmp_path),
        "requirement": "Verify existing URL expiration", "approval": "",
        "implementation_changed": False, "repair_applied": False, "retry_count": 0,
    }
    process = subprocess.CompletedProcess(
        args=["./mvnw", "clean", "test"], returncode=exit_code,
        stdout="BUILD SUCCESS" if exit_code == 0 else "BUILD FAILURE", stderr="diagnostics",
    )
    with patch("main.model", provider), patch(
        "agents.codebase_agent.get_model", return_value=provider
    ), patch("agents.validation_agent.subprocess.run", return_value=process) as run, patch(
        "main.implement_expiration"
    ) as implement, patch("main.apply_approved_repair") as repair, patch(
        "main.create_checkpoint"
    ) as checkpoint, patch("main.rollback") as rollback, patch("main.log_event"):
        paused = app.invoke(state, config=config)
        assert "__interrupt__" in paused
        run.assert_not_called()
        result = app.invoke(Command(resume=decision), config=config)
        if decision == "approve":
            run.assert_called_once_with(
                ["./mvnw", "clean", "test"], cwd=tmp_path / "url-shortener",
                capture_output=True, text=True, timeout=120, check=False,
            )
            assert result["validation_result"]["passed"] is (exit_code == 0)
            assert result["validation_result"]["exit_code"] == exit_code
            assert process.stdout in result["validation_result"]["output"]
            assert "diagnostics" in result["validation_result"]["output"]
            assert result["source_files_changed"] == 0
            assert result["tasks"][0]["status"] == ("COMPLETED" if exit_code == 0 else "FAILED")
        else:
            run.assert_not_called()
        for operation in (implement, repair, checkpoint, rollback):
            operation.assert_not_called()
    assert result["status"] == expected_status
    assert target.read_bytes() == original


def test_maven_timeout_is_reported_as_failure(tmp_path):
    (tmp_path / "url-shortener").mkdir()
    with patch("agents.validation_agent.subprocess.run", side_effect=subprocess.TimeoutExpired("mvnw", 120)):
        result = validate_implementation(tmp_path)
    assert result["passed"] is False
    assert result["exit_code"] == -1
    assert result["output"] == "Maven execution timed out"


@pytest.mark.parametrize("explicit_type", [False, True])
def test_implementation_tasks_keep_original_execution_path(tmp_path, explicit_type):
    target = tmp_path / IMPLEMENTATION_RELATIVE_PATH
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing source")
    task = {
        "task_id": "implementation", "description": "Implement approved expiration",
        "dependencies": [], "allowed_files": [IMPLEMENTATION_RELATIVE_PATH], "status": "READY",
    }
    if explicit_type:
        task["execution_type"] = "implementation"
    state = {
        "workflow_id": "implementation-regression", "workspace": str(tmp_path),
        "approval": "approve", "task_plan_valid": True,
        "active_task_ids": ["implementation"], "tasks": [task],
    }
    assert task_scheduler_router(state) == "implementation"
    with patch("main.create_checkpoint", return_value={"original_hash": "original"}) as checkpoint, patch(
        "main.implement_expiration", return_value=target
    ) as implement, patch("main.log_event"):
        result = implementation_node(state)
    checkpoint.assert_called_once_with(IMPLEMENTATION_RELATIVE_PATH, tmp_path)
    implement.assert_called_once_with(approved=True, workspace=tmp_path)
    assert result["status"] == "IMPLEMENTATION_COMPLETED"
    assert result["tasks"][0]["status"] == "RUNNING"
