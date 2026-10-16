"""CLI acceptance tests with real worktree, checkpoint, and audit persistence."""
import json
from pathlib import Path
import sqlite3
import subprocess
from unittest.mock import patch

import pytest

from governance.workspace_manager import initialize_workspace
from llm.provider import MockProvider
import main


@pytest.mark.parametrize("choice,exit_code,status", [
    ("1", 0, "AWAITING_FINAL_HUMAN_REVIEW"),
    ("1", 1, "STOPPED_VALIDATION_FAILED"),
    ("2", 0, "STOPPED"),
])
def test_cli_persists_approved_validation_result_and_audit(tmp_path, monkeypatch, capsys, choice, exit_code, status):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    relative = main.IMPLEMENTATION_RELATIVE_PATH
    source = repo / relative
    source.parent.mkdir(parents=True)
    source.write_text("package url_shortener; public class ShortUrl {}\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "Fixture"], cwd=repo, check=True, capture_output=True)
    original = source.read_bytes()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("governance.audit_logger.DB_PATH", tmp_path / "audit.db")
    inputs = iter(["Verify existing URL expiration", choice])
    provider = MockProvider("url-expiration")
    process = subprocess.CompletedProcess([], exit_code, "Maven result", "")
    real_run = subprocess.run
    with patch("builtins.input", side_effect=lambda prompt: next(inputs)), patch(
        "main.initialize_workspace", side_effect=lambda workflow_id: initialize_workspace(workflow_id, repo)
    ), patch("main.model", provider), patch("agents.codebase_agent.get_model", return_value=provider), patch(
        "llm.provider.OllamaProvider.invoke", side_effect=AssertionError("Ollama must not be contacted")
    ), patch("main.implement_expiration", side_effect=AssertionError("No source generation")), patch(
        "agents.validation_agent.subprocess.run", wraps=subprocess.run
    ) as run:
        # Allow real Git operations; replace only the Maven subprocess boundary.
        run.side_effect = lambda args, **kwargs: process if args[0] == "./mvnw" else real_run(args, **kwargs)
        result = main.main()
        maven_calls = [call for call in run.call_args_list if call.args[0][0] == "./mvnw"]
    assert len(maven_calls) == (1 if choice == "1" else 0)
    assert result["status"] == status
    assert source.read_bytes() == original
    assert (Path(result["workspace"]) / relative).read_bytes() == original
    assert json.loads(result["plan"])["tasks"][0]["execution_type"] == "validate_only"
    output = capsys.readouterr().out
    assert '"verify-expiration"' in output
    assert status in output
    if choice == "1":
        assert result["source_files_changed"] == 0
        assert "Source files changed: 0" in output
    with sqlite3.connect(tmp_path / "audit.db") as connection:
        events = connection.execute(
            "SELECT event, details FROM audit_events WHERE workflow_id=? ORDER BY id", (result["workflow_id"],)
        ).fetchall()
    names = [name for name, _ in events]
    assert names[0] == "WORKFLOW_STARTED"
    assert names[-1] == "WORKFLOW_FINISHED"
    assert json.loads(events[-1][1])["status"] == status
    if choice == "1":
        assert "PLAN_APPROVED" in names
        assert "VERIFICATION_STARTED" in names
        assert ("VALIDATION_PASSED" if exit_code == 0 else "VALIDATION_FAILED") in names
    else:
        assert "PLAN_REJECTED" in names
        assert "VERIFICATION_STARTED" not in names
    assert (tmp_path / "workflow.db").is_file()
