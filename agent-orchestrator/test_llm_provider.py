import pytest

from llm.provider import (
    get_model,
    MockProvider,
    OllamaProvider,
)


def test_mock_provider_selection(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    model = get_model()

    assert isinstance(model, MockProvider)


def test_ollama_provider_selection(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    model = get_model()

    assert isinstance(model, OllamaProvider)


def test_invalid_provider_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "invalid")

    with pytest.raises(ValueError):
        get_model()


def test_mock_provider_fails_explicitly(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    model = get_model()

    with pytest.raises(NotImplementedError):
        model.invoke("Analyze this requirement")

@pytest.mark.parametrize("stage", ["requirements", "codebase", "planning"])
def test_mock_routes_by_stage_only(stage):
    model = MockProvider(scenario="url-expiration")
    first = model.invoke("repair implementation planning requirements", stage=stage)
    second = model.invoke("unrelated prompt", stage=stage)
    assert first.text == second.text
    assert isinstance(first.text, str)


@pytest.mark.parametrize("stage", ["implementation", "repair"])
@pytest.mark.parametrize("scenario", ["unknown", "url-expiration"])
def test_unvetted_source_changes_fail_closed(stage, scenario):
    with pytest.raises(NotImplementedError, match="No vetted mock response"):
        MockProvider(scenario=scenario).invoke("Generate a change", stage=stage)


def test_unknown_stage_rejected():
    with pytest.raises(ValueError, match="Unsupported workflow stage"):
        MockProvider(scenario="url-expiration").invoke("requirements", stage="unknown")


def test_ollama_does_not_forward_stage(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    backend = Mock()
    backend.invoke.return_value = SimpleNamespace(content="real response")
    monkeypatch.setattr("llm.provider.ChatOllama", Mock(return_value=backend))
    result = OllamaProvider().invoke("prompt", stage="requirements")
    backend.invoke.assert_called_once_with("prompt")
    assert result.text == "real response"


def test_unknown_mock_scenario_preserves_source(tmp_path, monkeypatch):
    from agents import implementation_agent, repair_agent
    from governance.repair_executor import apply_approved_repair

    target = tmp_path / implementation_agent.ALLOWED_FILE
    target.parent.mkdir(parents=True)
    original = b"package url_shortener;\npublic class ShortUrl {}\n"
    target.write_bytes(original)
    model = MockProvider(scenario="unknown")
    monkeypatch.setattr(implementation_agent, "model", model)
    monkeypatch.setattr(repair_agent, "model", model)
    with pytest.raises(NotImplementedError):
        implementation_agent.implement_expiration(True, tmp_path)
    assert target.read_bytes() == original
    with pytest.raises(NotImplementedError):
        proposal = repair_agent.propose_repair("compile error", tmp_path)
        change = repair_agent.parse_repair_proposal(proposal)
        if change:
            apply_approved_repair(change, tmp_path)
    assert target.read_bytes() == original


def test_mock_requirements_response(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")
    response = get_model().invoke("Analyze URL expiration", stage="requirements")
    assert "URL expiration" in response.text


def test_mock_codebase_response(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")
    response = get_model().invoke("Analyze the Java codebase", stage="codebase")
    assert "ShortUrl.java" in response.text


def test_mock_planning_response(monkeypatch):
    import json
    from governance.task_orchestrator import tasks_from_plan, validate_task_plan

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")
    response = get_model().invoke("Create an implementation plan", stage="planning")
    payload = json.loads(response.text)
    assert "tasks" in payload
    assert payload["tasks"][0]["task_id"] == "verify-expiration"
    allowed_file = "url-shortener/src/main/java/url_shortener/ShortUrl.java"
    tasks = tasks_from_plan(response.text, allowed_file)
    validate_task_plan(tasks)
    # Verify parsing succeeded rather than silently using the prose fallback.
    assert tasks == [{**task, "status": "PENDING"} for task in payload["tasks"]]
    assert tasks[0]["task_id"] == "verify-expiration"
    assert tasks[0]["allowed_files"] == [allowed_file]


def test_mock_rejects_unvetted_implementation(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")
    model = get_model()
    with pytest.raises(NotImplementedError):
        model.invoke("Implement expiration", stage="implementation")


def test_mock_planning_produces_valid_task_dag(monkeypatch):
    import json
    from governance.task_orchestrator import tasks_from_plan, validate_task_plan

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")
    response = get_model().invoke("Create a task plan", stage="planning")
    payload = json.loads(response.text)
    assert len(payload["tasks"]) == 1
    assert payload["tasks"][0]["execution_type"] == "validate_only"
    tasks = tasks_from_plan(
        response.text,
        "url-shortener/src/main/java/url_shortener/ShortUrl.java",
    )
    assert tasks[0]["task_id"] == "verify-expiration"
    assert tasks[0]["dependencies"] == []
    validate_task_plan(tasks)
