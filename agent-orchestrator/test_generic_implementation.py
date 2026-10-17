from test_support import requirements_response
import json
from unittest.mock import Mock, patch

import pytest

from agents import implementation_agent
from governance.rollback_manager import create_checkpoint, file_hash, rollback
from governance.task_orchestrator import task_definition_hash
from llm.provider import ModelResponse, MockProvider


def setup_task(tmp_path, relative="url-shortener/src/main/java/url_shortener/Counter.java"):
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"package url_shortener; class Counter { int limit = 5; }\n")
    task = {"task_id": "increase-limit", "description": "Increase the counter limit to ten",
            "dependencies": [], "allowed_files": [relative], "status": "READY",
            "execution_type": "implementation"}
    context = {"approval": "approve", "tasks": [task], "active_task_ids": [task["task_id"]],
               "requirement": "Increase counter limit", "analysis": "Preserve other behavior",
               "plan": "Approved counter edit", "approved_task_hash": task_definition_hash([task])}
    return target, task, context


def test_generic_task_edits_non_expiration_source_and_can_rollback(tmp_path):
    target, task, context = setup_task(tmp_path)
    original = target.read_bytes()
    checkpoint = create_checkpoint(task["allowed_files"][0], tmp_path, allowed_files=task["allowed_files"])
    response = ModelResponse(json.dumps({"path": task["allowed_files"][0], "old_text": "limit = 5", "new_text": "limit = 10"}))
    model = Mock(invoke=Mock(return_value=response))
    with patch.object(implementation_agent, "model", model):
        transaction = implementation_agent.implement_task(task, tmp_path, context)
        assert transaction["paths"] == task["allowed_files"]
    assert b"limit = 10" in target.read_bytes()
    prompt = model.invoke.call_args.args[0]
    assert task["description"] in prompt
    assert context["requirement"] in prompt
    assert model.invoke.call_args.kwargs == {"stage": "implementation"}
    from governance.file_transaction import finish_transaction
    finish_transaction(transaction["transaction_id"], tmp_path, task["allowed_files"], passed=False)
    assert target.read_bytes() == original


@pytest.mark.parametrize("response", [
    "not JSON", json.dumps({"command": "rm -rf src"}),
    json.dumps({"path": "../outside.java", "old_text": "limit = 5", "new_text": "limit = 10"}),
    json.dumps({"path": "url-shortener/src/main/java/url_shortener/Counter.java", "old_text": "missing", "new_text": "replacement"}),
])
def test_invalid_model_edits_preserve_source(tmp_path, response):
    target, task, context = setup_task(tmp_path)
    original = target.read_bytes()
    with patch.object(implementation_agent, "model", Mock(invoke=Mock(return_value=ModelResponse(response)))):
        with pytest.raises((ValueError, PermissionError)):
            implementation_agent.implement_task(task, tmp_path, context)
    assert target.read_bytes() == original


@pytest.mark.parametrize("mutation", ["rejected", "tampered", "validate_only"])
def test_unapproved_and_nonimplementation_tasks_never_invoke_model(tmp_path, mutation):
    target, task, context = setup_task(tmp_path)
    if mutation == "rejected":
        context["approval"] = "reject"
    elif mutation == "tampered":
        task["description"] = "Unapproved replacement task"
    else:
        task["execution_type"] = "validate_only"
        context["approved_task_hash"] = task_definition_hash([task])
    with patch.object(implementation_agent, "model") as model:
        with pytest.raises(PermissionError):
            implementation_agent.implement_task(task, tmp_path, context)
        model.invoke.assert_not_called()


def test_unknown_mock_scenario_preserves_generic_source(tmp_path):
    target, task, context = setup_task(tmp_path)
    original = target.read_bytes()
    with patch.object(implementation_agent, "model", MockProvider("unknown")):
        with pytest.raises(NotImplementedError):
            implementation_agent.implement_task(task, tmp_path, context)
    assert target.read_bytes() == original


def test_concurrent_source_change_is_not_overwritten(tmp_path):
    target, task, context = setup_task(tmp_path)
    def invoke(prompt, *, stage):
        target.write_text("external change")
        return ModelResponse(json.dumps({"path": task["allowed_files"][0], "old_text": "limit = 5", "new_text": "limit = 10"}))
    with patch.object(implementation_agent, "model", Mock(invoke=invoke)):
        with pytest.raises(RuntimeError):
            implementation_agent.implement_task(task, tmp_path, context)
    assert target.read_text() == "external change"


@pytest.mark.parametrize("relative", ["url-shortener/pom.xml"])
def test_approved_task_cannot_expand_source_policy(tmp_path, relative):
    _, task, context = setup_task(tmp_path)
    task["allowed_files"] = [relative]
    context["approved_task_hash"] = task_definition_hash([task])
    with patch.object(implementation_agent, "model") as model:
        with pytest.raises(PermissionError):
            implementation_agent.implement_task(task, tmp_path, context)
        model.invoke.assert_not_called()


def test_symlink_escape_never_invokes_model(tmp_path):
    target, task, context = setup_task(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + '-outside.java')
    outside.write_text('outside source')
    try:
        target.unlink()
        target.symlink_to(outside)
        with patch.object(implementation_agent, "model") as model:
            with pytest.raises(PermissionError):
                implementation_agent.implement_task(task, tmp_path, context)
            model.invoke.assert_not_called()
        assert outside.read_text() == 'outside source'
    finally:
        outside.unlink()


def test_blocked_implementation_stops_graph_before_maven(tmp_path):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from main import workflow
    target, task, context = setup_task(tmp_path)
    original = target.read_bytes()
    responses = iter([
        requirements_response('Counter requirement'),
        ModelResponse(json.dumps({'tasks': [{key: value for key, value in task.items() if key != 'status'}]})),
    ])
    app = workflow.compile(checkpointer=InMemorySaver())
    config = {'configurable': {'thread_id': 'blocked-generic'}}
    state = {'workflow_id': 'blocked-generic', 'workspace': str(tmp_path), 'requirement': 'Increase counter limit'}
    with patch('main.model', Mock(invoke=Mock(side_effect=lambda *args, **kwargs: next(responses)))), patch(
        'main.analyze_codebase', return_value='Counter source'
    ), patch.object(implementation_agent, 'model', MockProvider('unknown')), patch(
        'main.validate_implementation'
    ) as validate, patch('main.log_event') as audit:
        paused = app.invoke(state, config=config)
        assert '__interrupt__' in paused
        result = app.invoke(Command(resume='approve'), config=config)
        validate.assert_not_called()
        assert 'IMPLEMENTATION_BLOCKED' in [call.kwargs['event'] for call in audit.call_args_list]
    assert result['status'] == 'STOPPED_IMPLEMENTATION_BLOCKED'
    assert target.read_bytes() == original


def test_source_policy_rejects_traversal(tmp_path):
    from governance.source_policy import authorized_java_target
    with pytest.raises(PermissionError):
        authorized_java_target("../outside.java", tmp_path, ["../outside.java"])


@pytest.mark.parametrize('passed', [True, False])
def test_generic_graph_validates_then_reviews_or_restores(tmp_path, passed):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command
    from main import workflow
    from governance import audit_logger
    import sqlite3

    target, task, _ = setup_task(tmp_path)
    original = target.read_bytes()
    plan = json.dumps({'tasks': [{key: value for key, value in task.items() if key != 'status'}]})
    analysis_model = Mock(invoke=Mock(side_effect=[requirements_response('Counter requirement'), ModelResponse(plan)]))
    edit = ModelResponse(json.dumps({'path': task['allowed_files'][0], 'old_text': 'limit = 5', 'new_text': 'limit = 10'}))
    state = {'workflow_id': 'generic-result', 'workspace': str(tmp_path), 'requirement': 'Increase counter limit',
             'implementation_changed': False, 'repair_applied': False}
    config = {'configurable': {'thread_id': 'generic-result'}}
    validation = {'passed': passed, 'exit_code': 0 if passed else 1, 'duration_seconds': 0.1, 'output': 'Maven result'}
    with patch('main.model', analysis_model), patch('main.analyze_codebase', return_value='Counter'), patch.object(
        implementation_agent, 'model', Mock(invoke=Mock(return_value=edit))
    ), patch('main.validate_implementation', return_value=validation) as validate, patch.object(
        audit_logger, 'DB_PATH', tmp_path / 'audit.db'
    ), SqliteSaver.from_conn_string(str(tmp_path / 'workflow.db')) as saver:
        app = workflow.compile(checkpointer=saver)
        assert '__interrupt__' in app.invoke(state, config=config)
        assert target.read_bytes() == original
        result = app.invoke(Command(resume='approve'), config=config)
        validate.assert_called_once_with(tmp_path)
        assert app.get_state(config).values['status'] == result['status']
    with sqlite3.connect(tmp_path / 'audit.db') as conn:
        events = [row[0] for row in conn.execute('SELECT event FROM audit_events ORDER BY id')]
    if passed:
        assert result['status'] == 'AWAITING_FINAL_HUMAN_REVIEW'
        assert b'limit = 10' in target.read_bytes()
        assert 'VALIDATION_PASSED' in events
    else:
        assert result['status'] == 'STOPPED_ROLLED_BACK'
        assert target.read_bytes() == original
        assert 'VALIDATION_FAILED' in events
        assert 'ROLLBACK_COMPLETED' in events
        assert 'AWAITING_FINAL_HUMAN_REVIEW' not in events
