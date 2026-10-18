import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import governance.repair_executor as repair_executor
from main import repair_executor_node, retry_node, validation_node


RELATIVE_JAVA_FILE = "url-shortener/src/main/java/url_shortener/ShortUrl.java"


def _workspace(initial_text: str):
    directory = TemporaryDirectory()
    target = Path(directory.name) / RELATIVE_JAVA_FILE
    target.parent.mkdir(parents=True)
    target.write_text(initial_text, encoding="utf-8")
    return directory, target


def test_approved_repair_is_exact_and_checkpointed():
    directory, target = _workspace("class ShortUrl {\n    String oldValue;\n}\n")
    try:
        result = repair_executor.apply_approved_repair(
            {
                "path": RELATIVE_JAVA_FILE,
                "old_text": "String oldValue;",
                "new_text": "String expiresAt;",
            }, Path(directory.name)
        )

        assert target.read_text(encoding="utf-8") == (
            "class ShortUrl {\n    String expiresAt;\n}\n"
        )
        assert result["checkpoint"]["original_content"] == (
            b"class ShortUrl {\n    String oldValue;\n}\n"
        )
        assert result["changed"] is True
    finally:
        directory.cleanup()


def test_unauthorized_path_is_rejected_before_writing():
    directory, target = _workspace("safe")
    try:
        try:
            repair_executor.apply_approved_repair(
                {
                    "path": "../pom.xml",
                    "old_text": "safe",
                    "new_text": "unsafe",
                }, Path(directory.name)
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("Unauthorized path was accepted")
        assert target.read_text(encoding="utf-8") == "safe"
    finally:
        directory.cleanup()


def test_symlink_escape_is_rejected():
    directory, target = _workspace("safe")
    outside_directory = TemporaryDirectory()
    outside = Path(outside_directory.name) / "ShortUrl.java"
    outside.write_text("outside", encoding="utf-8")
    try:
        target.unlink()
        target.symlink_to(outside)
        try:
            repair_executor.apply_approved_repair(
                {
                    "path": RELATIVE_JAVA_FILE,
                    "old_text": "outside",
                    "new_text": "modified",
                }, Path(directory.name)
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("Symlink escape was accepted")
        assert outside.read_text(encoding="utf-8") == "outside"
    finally:
        target.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)
        outside_directory.cleanup()
        directory.cleanup()


def test_rejection_never_calls_executor():
    state = {
        "workspace": "/tmp/test-workflow",
        "workflow_id": "rejection-test",
        "repair_approval": "reject",
        "repair_change": {
            "path": RELATIVE_JAVA_FILE,
            "old_text": "a",
            "new_text": "b",
        },
    }
    with patch("main.apply_approved_repair") as apply:
        result = repair_executor_node(state)
    apply.assert_not_called()
    assert result["status"] == "STOPPED_REPAIR_NOT_APPLIED"


def test_approval_executes_only_the_structured_change():
    change = {
        "path": RELATIVE_JAVA_FILE,
        "old_text": "a",
        "new_text": "b",
    }
    execution = {
        "path": RELATIVE_JAVA_FILE,
        "checkpoint": {"path": RELATIVE_JAVA_FILE},
        "expected_current_hash": "new-hash",
        "changed": True,
    }
    state = {
        "workspace": "/tmp/test-workflow",
        "workflow_id": "approval-test",
        "repair_approval": "approve",
        "repair_change": change,
    }
    with patch("main.apply_approved_repair", return_value=execution) as apply, patch(
        "main.log_event"
    ) as audit:
        result = repair_executor_node(state)

    apply.assert_called_once_with(change, Path(state["workspace"]))
    assert result["repair_applied"] is True
    assert result["repair_checkpoint"] == execution["checkpoint"]
    assert audit.call_args.kwargs["event"] == "REPAIR_EXECUTED"


def test_mocked_llm_proposal_is_parsed_but_not_executed():
    proposal = json.dumps(
        {
            "path": RELATIVE_JAVA_FILE,
            "old_text": "old",
            "new_text": "new",
            "reason": "deterministic test",
        }
    )
    state = {
        "workspace": "/tmp/test-workflow",
        "workflow_id": "proposal-test",
        "retry_count": 0,
        "validation_result": {"output": "compile failure"},
    }
    with patch("main.propose_repair", return_value=proposal), patch(
        "main.log_event"
    ):
        result = retry_node(state)

    assert result["repair_change"] == {
        "path": RELATIVE_JAVA_FILE,
        "old_text": "old",
        "new_text": "new",
    }
    assert result["retry_count"] == 1


def test_failed_validation_rolls_back_repair_and_records_event():
    state = {
        "workspace": "/tmp/test-workflow",
        "workflow_id": "rollback-test",
        "retry_count": 1,
        "repair_applied": True,
        "repair_rolled_back": False,
        "repair_checkpoint": {"path": RELATIVE_JAVA_FILE},
        "repair_expected_current_hash": "expected",
    }
    failed = {
        "passed": False,
        "exit_code": 1,
        "duration_seconds": 0.1,
        "output": "Maven failure",
    }
    with patch("main.validate_implementation", return_value=failed), patch(
        "main.rollback", return_value=True
    ) as rollback, patch("main.log_event") as audit:
        result = validation_node(state)

    rollback.assert_called_once_with(state["repair_checkpoint"], "expected", Path(state["workspace"]))
    assert result["repair_applied"] is False
    assert result["repair_rolled_back"] is True
    events = [call.kwargs["event"] for call in audit.call_args_list]
    assert "VALIDATION_FAILED" in events
    assert "REPAIR_ROLLBACK_COMPLETED" in events


def test_failed_rollback_blocks_further_retries():
    state = {
        "workspace": "/tmp/test-workflow",
        "workflow_id": "blocked-rollback-test",
        "retry_count": 1,
        "repair_applied": True,
        "repair_rolled_back": False,
        "repair_checkpoint": {"path": RELATIVE_JAVA_FILE},
        "repair_expected_current_hash": "expected",
    }
    failed = {
        "passed": False,
        "exit_code": 1,
        "duration_seconds": 0.1,
        "output": "Maven failure",
    }
    with patch("main.validate_implementation", return_value=failed), patch(
        "main.rollback", side_effect=RuntimeError("unexpected file change")
    ), patch("main.log_event") as audit:
        result = validation_node(state)

    assert result["repair_rollback_blocked"] is True
    events = [call.kwargs["event"] for call in audit.call_args_list]
    assert "REPAIR_ROLLBACK_BLOCKED" in events
