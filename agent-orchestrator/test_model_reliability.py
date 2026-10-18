import os
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sqlite3

import json

from llm.provider import (
    ModelResponse,
    ResilientModelProvider,
    ModelUnavailableError,
    OllamaProvider,
    MockProvider,
)
from governance.audit_logger import DB_PATH
from governance import model_attempts


def get_events(workflow_id: str, db_path: Path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT workflow_id, timestamp, agent, event, details FROM audit_events WHERE workflow_id = ?",
            (workflow_id,)
        ).fetchall()
        return [
            {
                "workflow_id": r["workflow_id"],
                "timestamp": r["timestamp"],
                "agent": r["agent"],
                "event": r["event"],
                "details": json.loads(r["details"]) if r["details"] else {}
            }
            for r in rows
        ]


def test_bounded_retry_transient_failure_success(tmp_path, monkeypatch):
    """A transient error retries within max_retries budget and succeeds."""
    db_file = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)

    primary = MagicMock(spec=OllamaProvider)
    primary.model_name = "qwen2.5-coder:7b"

    calls = []
    def fail_then_succeed(prompt, *, stage=None):
        calls.append(prompt)
        if len(calls) == 1:
            raise ConnectionError("Temporary connection failure to Ollama")
        return ModelResponse(text="Generated response after retry")

    primary.invoke.side_effect = fail_then_succeed

    provider = ResilientModelProvider(
        primary=primary,
        max_retries=2,
        is_live=False,
        workflow_id="retry-test-wf",
    )

    result = provider.invoke("hello world", stage="requirements")
    assert result.text == "Generated response after retry"
    assert len(calls) == 2


def test_real_fallback_when_primary_exhausted(tmp_path, monkeypatch):
    """When primary exhausts its retry budget, fallback model is engaged."""
    db_file = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)

    primary = MagicMock(spec=OllamaProvider)
    primary.model_name = "primary-model"
    primary.invoke.side_effect = TimeoutError("Primary timeout")

    fallback = MagicMock(spec=OllamaProvider)
    fallback.model_name = "fallback-model"
    fallback.invoke.return_value = ModelResponse(text="Response from real fallback")

    provider = ResilientModelProvider(
        primary=primary,
        fallback=fallback,
        max_retries=1,
        is_live=False,
        workflow_id="fallback-test-wf",
    )

    result = provider.invoke("test fallback prompt", stage="design")
    assert result.text == "Response from real fallback"
    assert primary.invoke.call_count == 2  # initial + 1 retry
    assert fallback.invoke.call_count == 1

    events = get_events("fallback-test-wf", db_file)
    fallback_events = [e for e in events if e["event"] == "MODEL_PROVIDER_FALLBACK"]
    assert len(fallback_events) == 1
    assert fallback_events[0]["details"]["primary_provider"] == "primary-model"
    assert fallback_events[0]["details"]["fallback_provider"] == "fallback-model"


def test_stopped_model_unavailable_when_all_providers_exhausted(tmp_path, monkeypatch):
    """When primary and all fallbacks fail, raises ModelUnavailableError."""
    db_file = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)

    primary = MagicMock(spec=OllamaProvider)
    primary.model_name = "failing-primary"
    primary.invoke.side_effect = ConnectionRefusedError("Connection refused")

    provider = ResilientModelProvider(
        primary=primary,
        fallback=None,
        max_retries=1,
        is_live=False,
        workflow_id="exhaustion-test-wf",
    )

    with pytest.raises(ModelUnavailableError) as exc_info:
        provider.invoke("failing prompt", stage="implementation")

    assert "All authorized model providers failed" in str(exc_info.value)

    events = get_events("exhaustion-test-wf", db_file)
    exhaust_events = [e for e in events if e["event"] == "MODEL_RETRIES_EXHAUSTED"]
    assert len(exhaust_events) >= 1
    assert exhaust_events[0]["details"]["provider"] == "failing-primary"

    unavailable_events = [e for e in events if e["event"] == "MODEL_UNAVAILABLE"]
    assert len(unavailable_events) == 1
    assert unavailable_events[0]["details"]["total_attempts"] == 2


def test_mock_fallback_strictly_prohibited_in_live_mode(tmp_path, monkeypatch):
    """In live workflows, fallback to MockProvider raises PermissionError/SecurityError."""
    db_file = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)

    primary = MagicMock(spec=OllamaProvider)
    primary.model_name = "live-ollama"
    primary.invoke.side_effect = ConnectionError("Live connection failed")

    mock_fb = MockProvider("url-expiration")

    provider = ResilientModelProvider(
        primary=primary,
        fallback=mock_fb,
        max_retries=1,
        is_live=True,  # LIVE WORKFLOW
        workflow_id="live-security-wf",
    )

    with pytest.raises(PermissionError) as exc_info:
        provider.invoke("live code change prompt", stage="implementation")

    assert "Mock provider responses are strictly prohibited in live workflows" in str(exc_info.value)


def test_audit_logging_no_sensitive_prompts_or_credentials(tmp_path, monkeypatch):
    """Audit logs for model events must never contain sensitive prompts or tokens."""
    db_file = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)

    primary = MagicMock(spec=OllamaProvider)
    primary.model_name = "audit-ollama"
    secret_prompt = "TOP_SECRET_PROMPT_ABC123 with user password sensitive-api-token"
    primary.invoke.side_effect = RuntimeError("Fatal model error")

    provider = ResilientModelProvider(
        primary=primary,
        fallback=None,
        max_retries=1,
        is_live=False,
        workflow_id="audit-leak-test",
    )

    with pytest.raises(ModelUnavailableError):
        provider.invoke(secret_prompt, stage="planning")

    events = get_events("audit-leak-test", db_file)
    assert len(events) > 0
    for event in events:
        details_str = str(event["details"])
        assert "TOP_SECRET_PROMPT_ABC123" not in details_str
        assert "sensitive-api-token" not in details_str


def test_retry_budget_exhaustion_across_restarts_and_ledger(tmp_path, monkeypatch):
    """Durable ledger records failures and prevents resetting attempt budgets across restarts."""
    db_file = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)

    # Initialize ledger table with exhausted attempts using governance.model_attempts
    for ord_idx in range(1, 4):
        ident = model_attempts.start(db_file, "exhausted-ledger-wf", ord_idx, {"receipt": ord_idx}, f"prompt-{ord_idx}", "test-model")
        model_attempts.fail(db_file, ident, RuntimeError("Exhaustion failure"))

    primary = MagicMock(spec=OllamaProvider)
    primary.model_name = "ledger-test-model"

    provider = ResilientModelProvider(
        primary=primary,
        max_retries=2,
        is_live=False,
        workflow_id="exhausted-ledger-wf",
        attempts_db=db_file,
    )

    with pytest.raises(PermissionError) as exc_info:
        provider.invoke("attempt prompt", stage="implementation")

    assert "Attempt budget exhausted in durable ledger" in str(exc_info.value)
    # Ensure primary was never called because ledger blocked it upfront
    assert primary.invoke.call_count == 0
