"""Tests for checkpointed final human review, engineering acceptance, and stale approval safeguards."""
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch, Mock

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from governance.audit_logger import DB_PATH
from llm.provider import MockProvider, ModelResponse
import main
from main import (
    EngineeringState,
    workflow,
    compute_final_review_bundle,
    final_human_review_node,
    _task_definition_hash,
)


def _setup_workspace(tmp_path):
    repo = tmp_path / "workspace"
    repo.mkdir(parents=True, exist_ok=True)
    relative = main.IMPLEMENTATION_RELATIVE_PATH
    source = repo / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("package url_shortener;\npublic class ShortUrl {}\n")
    return repo, source


def _base_state(repo, workflow_id="final-review-test"):
    return {
        "workspace": str(repo),
        "workflow_id": workflow_id,
        "requirement": "Verify existing URL expiration",
        "requirement_version": 1,
        "approval": "",
        "plan": "",
        "plan_version": 1,
        "tasks": [],
        "active_task_ids": [],
        "task_plan_valid": False,
        "replan_history": [],
        "attempt_history": [],
        "validation_result": {},
        "retry_count": 0,
        "implementation_changed": False,
        "repair_applied": False,
        "repair_rolled_back": False,
        "repair_rollback_blocked": False,
        "final_human_review_required": True,
        "engineering_accepted": False,
        "production_release_authorized": False,
        "final_review_decision": "",
        "final_review_bundle_hash": "",
        "final_review_artifact_hashes": {},
        "final_review_error": "",
        "status": "STARTED",
    }


def test_final_human_review_interrupts_and_approves(tmp_path, monkeypatch):
    db_file = tmp_path / "audit.db"
    wf_db = tmp_path / "workflow.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")
    repo, source = _setup_workspace(tmp_path)
    state = _base_state(repo)

    passed_validation = {"passed": True, "exit_code": 0, "duration_seconds": 0.1, "output": "BUILD SUCCESS"}
    provider = MockProvider("url-expiration")

    with patch("main.validate_implementation", return_value=passed_validation), \
         patch("main.model", provider), \
         patch("agents.codebase_agent.get_model", return_value=provider), \
         SqliteSaver.from_conn_string(str(wf_db)) as checkpointer:

        app = workflow.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": state["workflow_id"]}}

        # First interrupt: plan approval
        paused = app.invoke(state, config=config)
        assert "__interrupt__" in paused
        assert paused["__interrupt__"][0].value["requirement"] == "Verify existing URL expiration"

        # Resume plan approval -> reaches validation -> reaches final_human_review interrupt
        review_paused = app.invoke(Command(resume="approve"), config=config)
        assert "__interrupt__" in review_paused
        payload = review_paused["__interrupt__"][0].value
        assert payload["type"] == "FINAL_HUMAN_REVIEW"
        assert payload["workflow_id"] == state["workflow_id"]
        assert payload["plan_version"] == 2
        assert "bundle_hash" in payload
        assert "disclaimer" in payload
        assert "NOT authorize production release" in payload["disclaimer"]

        # Resume final human review with approve
        result = app.invoke(Command(resume="approve"), config=config)
        assert result["status"] == "ENGINEERING_ACCEPTED"
        assert result["engineering_accepted"] is True
        assert result["production_release_authorized"] is False
        assert result["final_review_decision"] == "approve"
        assert result["final_review_bundle_hash"] == payload["bundle_hash"]

    # Verify audit log
    with sqlite3.connect(db_file) as conn:
        events = conn.execute(
            "SELECT event, details FROM audit_events WHERE workflow_id=? ORDER BY id",
            (state["workflow_id"],)
        ).fetchall()
    event_names = [e[0] for e in events]
    assert "FINAL_REVIEW_REQUESTED" in event_names
    assert "FINAL_REVIEW_APPROVED" in event_names

    approved_details = json.loads([e[1] for e in events if e[0] == "FINAL_REVIEW_APPROVED"][0])
    assert approved_details["engineering_accepted"] is True
    assert approved_details["production_release_authorized"] is False
    assert approved_details["bundle_hash"] == payload["bundle_hash"]


def test_final_human_review_explicit_rejection(tmp_path, monkeypatch):
    db_file = tmp_path / "audit.db"
    wf_db = tmp_path / "workflow.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")
    repo, source = _setup_workspace(tmp_path)
    state = _base_state(repo, workflow_id="reject-test")

    passed_validation = {"passed": True, "exit_code": 0, "duration_seconds": 0.1, "output": "BUILD SUCCESS"}
    provider = MockProvider("url-expiration")

    with patch("main.validate_implementation", return_value=passed_validation), \
         patch("main.model", provider), \
         patch("agents.codebase_agent.get_model", return_value=provider), \
         SqliteSaver.from_conn_string(str(wf_db)) as checkpointer:

        app = workflow.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": state["workflow_id"]}}

        app.invoke(state, config=config)
        review_paused = app.invoke(Command(resume="approve"), config=config)
        assert review_paused["__interrupt__"][0].value["type"] == "FINAL_HUMAN_REVIEW"

        result = app.invoke(Command(resume="reject"), config=config)
        assert result["status"] == "STOPPED_FINAL_REVIEW_REJECTED"
        assert result["engineering_accepted"] is False
        assert result["production_release_authorized"] is False
        assert result["final_review_decision"] == "reject"

    with sqlite3.connect(db_file) as conn:
        events = conn.execute(
            "SELECT event, details FROM audit_events WHERE workflow_id=? ORDER BY id",
            (state["workflow_id"],)
        ).fetchall()
    event_names = [e[0] for e in events]
    assert "FINAL_REVIEW_REQUESTED" in event_names
    assert "FINAL_REVIEW_REJECTED" in event_names


def test_production_release_authorization_strictly_disallowed(tmp_path, monkeypatch):
    """Verify that final review cannot grant production release authority under any input."""
    db_file = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)
    repo, source = _setup_workspace(tmp_path)
    state = _base_state(repo, workflow_id="no-release-auth")
    state["status"] = "AWAITING_FINAL_HUMAN_REVIEW"

    # Direct node invocation
    with patch("main.interrupt", return_value="approve"):
        result = final_human_review_node(state)
        assert result["engineering_accepted"] is True
        assert result["production_release_authorized"] is False

    # Attempt to pass a payload trying to override production release
    with patch("main.interrupt", return_value={"decision": "approve", "production_release_authorized": True}):
        result = final_human_review_node(state)
        assert result["production_release_authorized"] is False


def test_stale_approval_rejected_when_artifacts_modified(tmp_path, monkeypatch):
    """If workspace source files are modified during interrupt pause, approval is rejected."""
    db_file = tmp_path / "audit.db"
    wf_db = tmp_path / "workflow.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")
    repo, source = _setup_workspace(tmp_path)
    state = _base_state(repo, workflow_id="stale-test")

    passed_validation = {"passed": True, "exit_code": 0, "duration_seconds": 0.1, "output": "BUILD SUCCESS"}
    provider = MockProvider("url-expiration")

    with patch("main.validate_implementation", return_value=passed_validation), \
         patch("main.model", provider), \
         patch("agents.codebase_agent.get_model", return_value=provider), \
         SqliteSaver.from_conn_string(str(wf_db)) as checkpointer:

        app = workflow.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": state["workflow_id"]}}

        app.invoke(state, config=config)
        review_paused = app.invoke(Command(resume="approve"), config=config)
        assert review_paused["__interrupt__"][0].value["type"] == "FINAL_HUMAN_REVIEW"

        # Tamper with the artifact while paused
        source.write_text("package url_shortener;\npublic class ShortUrl { /* tampered */ }\n")

        # Attempt to approve stale artifact
        result = app.invoke(Command(resume="approve"), config=config)
        assert result["status"] == "STOPPED_FINAL_REVIEW_REJECTED"
        assert result["engineering_accepted"] is False
        assert "Stale" in result.get("final_review_error", "")

    with sqlite3.connect(db_file) as conn:
        events = conn.execute(
            "SELECT event, details FROM audit_events WHERE workflow_id=? ORDER BY id",
            (state["workflow_id"],)
        ).fetchall()
    rejected_events = [json.loads(d) for ev, d in events if ev == "FINAL_REVIEW_REJECTED"]
    assert len(rejected_events) == 1
    assert rejected_events[0]["reason"] == "stale_artifact_hash"


def test_checkpoint_resumption_across_fresh_process(tmp_path, monkeypatch):
    """Durable SQLite checkpoint allows resuming review from a completely new runner."""
    db_file = tmp_path / "audit.db"
    wf_db = tmp_path / "workflow.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")
    repo, source = _setup_workspace(tmp_path)
    state = _base_state(repo, workflow_id="durable-resume-test")

    passed_validation = {"passed": True, "exit_code": 0, "duration_seconds": 0.1, "output": "BUILD SUCCESS"}
    provider = MockProvider("url-expiration")

    # Process 1: run to final review interrupt and exit
    with patch("main.validate_implementation", return_value=passed_validation), \
         patch("main.model", provider), \
         patch("agents.codebase_agent.get_model", return_value=provider), \
         SqliteSaver.from_conn_string(str(wf_db)) as checkpointer1:
        app1 = workflow.compile(checkpointer=checkpointer1)
        config = {"configurable": {"thread_id": state["workflow_id"]}}
        app1.invoke(state, config=config)
        paused = app1.invoke(Command(resume="approve"), config=config)
        assert paused["__interrupt__"][0].value["type"] == "FINAL_HUMAN_REVIEW"

    # Process 2: open new SQLite connection to same db and resume
    with patch("main.model", provider), \
         patch("agents.codebase_agent.get_model", return_value=provider), \
         SqliteSaver.from_conn_string(str(wf_db)) as checkpointer2:
        app2 = workflow.compile(checkpointer=checkpointer2)
        saved_state = app2.get_state(config)
        assert saved_state.next == ("final_human_review",)

        result = app2.invoke(Command(resume="approve"), config=config)
        assert result["status"] == "ENGINEERING_ACCEPTED"
        assert result["engineering_accepted"] is True
        assert result["production_release_authorized"] is False
