"""Automated tests for unified routing, parallel eligibility, sequential fallback, and approval enforcement."""

import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from governance.task_orchestrator import is_parallel_eligible
from llm.provider import MockProvider
import main
from main import (
    EngineeringState,
    execution_strategy_router,
    workflow,
    initialize_workspace,
)


def _make_task(task_id: str, allowed_file: str, execution_type: str = "implementation", dependencies: list | None = None, status: str = "READY") -> dict:
    return {
        "task_id": task_id,
        "description": f"Implement or test {task_id}",
        "execution_type": execution_type,
        "allowed_files": [allowed_file],
        "dependencies": dependencies or [],
        "status": status,
    }


def test_parallel_eligibility_multi_task_disjoint():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
        _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java"),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is True
    assert "Eligible for dependency-aware parallel execution" in reason


def test_parallel_eligibility_single_task():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "requires 2 to 6 tasks (found 1)" in reason


def test_parallel_eligibility_too_many_tasks():
    tasks = [
        _make_task(f"task-{i}", f"url-shortener/src/main/java/url_shortener/File{i}.java")
        for i in range(7)
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "requires 2 to 6 tasks (found 7)" in reason


def test_parallel_eligibility_overlapping_files():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
        _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlService.java"),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "overlap" in reason.lower()


def test_parallel_eligibility_validation_only():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java", execution_type="validate_only"),
        _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java"),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "non-implementation execution_type" in reason


def test_parallel_eligibility_cyclic_dependency():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java", dependencies=["task-2"]),
        _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java", dependencies=["task-1"]),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "Circular" in reason


def test_execution_strategy_router_parallel():
    state = {
        "auto_orchestration": True,
        "tasks": [
            _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
            _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java"),
        ],
    }
    assert execution_strategy_router(state) == "parallel"


def test_execution_strategy_router_sequential_fallback():
    state = {
        "auto_orchestration": True,
        "tasks": [
            _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
        ],
    }
    assert execution_strategy_router(state) == "legacy"


def test_execution_strategy_router_sequential_fallback_full_sdlc():
    state = {
        "auto_orchestration": True,
        "full_sdlc": True,
        "tasks": [
            _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
        ],
    }
    assert execution_strategy_router(state) == "integrated"


def test_execution_strategy_router_explicit_flags():
    assert execution_strategy_router({"parallel_evidence": "/tmp/pe"}) == "parallel"
    assert execution_strategy_router({"integrated_sdlc": True, "integration_evidence": "/tmp/ie"}) == "integrated"


def test_execution_strategy_router_disabled_auto_orchestration():
    state = {
        "auto_orchestration": False,
        "tasks": [
            _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
            _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java"),
        ],
    }
    assert execution_strategy_router(state) == "legacy"


def test_plan_rejection_stops_execution(tmp_path, monkeypatch):
    db_file = tmp_path / "audit.db"
    wf_db = tmp_path / "workflow.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    source = repo / main.IMPLEMENTATION_RELATIVE_PATH
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("package url_shortener; public class ShortUrl {}\n")

    provider = MockProvider("url-expiration")

    with patch("main.model", provider), \
         patch("agents.codebase_agent.get_model", return_value=provider), \
         SqliteSaver.from_conn_string(str(wf_db)) as checkpointer:

        app = workflow.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "reject-flow"}}

        state = {
            "workspace": str(repo),
            "workflow_id": "reject-flow",
            "requirement": "Verify existing URL expiration",
            "requirement_version": 1,
            "status": "STARTED",
            "approval": "",
            "plan": "",
            "tasks": [],
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
        }

        # Pauses at plan approval
        paused = app.invoke(state, config=config)
        assert "__interrupt__" in paused

        # Resume with reject
        result = app.invoke(Command(resume="reject"), config=config)
        assert result["status"] == "STOPPED"
        assert result["approval"] == "reject"

    # Verify audit event recorded
    with sqlite3.connect(db_file) as conn:
        events = conn.execute(
            "SELECT event FROM audit_events WHERE workflow_id=?",
            ("reject-flow",)
        ).fetchall()
    event_names = [e[0] for e in events]
    assert "PLAN_REJECTED" in event_names
