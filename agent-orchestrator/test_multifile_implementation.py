from test_support import requirements_response
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from agents import implementation_agent
from governance import file_transaction as transactions
from governance.rollback_manager import file_hash
from governance.task_orchestrator import task_definition_hash
from llm.provider import ModelResponse

PATHS = [f'url-shortener/src/main/java/url_shortener/{name}.java' for name in ('UrlService', 'UrlController')]


def setup_task(tmp_path):
    originals = {}
    for name, relative in zip(('UrlService', 'UrlController'), PATHS):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        originals[relative] = f'package url_shortener; class {name} {{ int oldValue = 1; }}\n'.encode()
        target.write_bytes(originals[relative])
    task = {'task_id': 'coordinated-api', 'description': 'Implement the coordinated API enhancement',
            'allowed_files': PATHS.copy(), 'execution_type': 'implementation', 'dependencies': [], 'status': 'READY'}
    context = {'workflow_id': 'multifile-test', 'approval': 'approve', 'tasks': [task],
               'active_task_ids': [task['task_id']], 'approved_task_hash': task_definition_hash([task])}
    edits = [{'path': relative, 'old_text': 'oldValue = 1', 'new_text': 'newValue = 2'} for relative in PATHS]
    return task, context, edits, originals


def execute(tmp_path, task, context, edits):
    with patch.object(implementation_agent, 'model', Mock(invoke=Mock(return_value=ModelResponse(json.dumps({'edits': edits}))))):
        return implementation_agent.implement_task(task, tmp_path, context)


def assert_originals(tmp_path, originals):
    for relative, original in originals.items():
        assert (tmp_path / relative).read_bytes() == original


@pytest.mark.parametrize('bad_edit', ['unauthorized', 'mismatch', 'duplicate', 'missing'])
def test_entire_proposal_is_validated_before_any_write(tmp_path, bad_edit):
    task, context, edits, originals = setup_task(tmp_path)
    if bad_edit == 'unauthorized':
        edits[1]['path'] = 'url-shortener/pom.xml'
    elif bad_edit == 'mismatch':
        edits[1]['old_text'] = 'absent text'
    elif bad_edit == 'duplicate':
        edits[1] = edits[0].copy()
    else:
        edits.pop()
    with pytest.raises((PermissionError, ValueError)):
        execute(tmp_path, task, context, edits)
    assert_originals(tmp_path, originals)
    assert not (tmp_path / '.agent-transactions').exists()


def test_changes_during_model_generation_prevent_all_writes(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    def invoke(*args, **kwargs):
        (tmp_path / PATHS[1]).write_bytes(b'external change')
        return ModelResponse(json.dumps({'edits': edits}))
    with patch.object(implementation_agent, 'model', Mock(invoke=invoke)):
        with pytest.raises(RuntimeError):
            implementation_agent.implement_task(task, tmp_path, context)
    assert (tmp_path / PATHS[0]).read_bytes() == originals[PATHS[0]]
    assert (tmp_path / PATHS[1]).read_bytes() == b'external change'


def test_second_file_write_failure_restores_the_first_file(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    real_replace = transactions.os.replace
    failed = False
    def replace(source, target):
        nonlocal failed
        if Path(target) == tmp_path / PATHS[1] and not failed:
            failed = True
            raise OSError('Injected second replacement failure')
        return real_replace(source, target)
    with patch.object(transactions.os, 'replace', side_effect=replace), patch.object(transactions, 'log_event') as audit:
        with pytest.raises(OSError):
            execute(tmp_path, task, context, edits)
    assert failed
    assert_originals(tmp_path, originals)
    journal = json.loads(next((tmp_path / '.agent-transactions').glob('*.json')).read_text())
    assert journal['status'] == 'rolled_back'
    assert not list(tmp_path.rglob('.agent-edit-*'))
    assert 'TRANSACTION_APPLY_ROLLED_BACK' in [call.args[2] for call in audit.call_args_list]


def test_rollback_preflights_all_files_before_restoring_any(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    result = execute(tmp_path, task, context, edits)
    first_applied = (tmp_path / PATHS[0]).read_bytes()
    (tmp_path / PATHS[1]).write_bytes(b'external change')
    with pytest.raises(transactions.RecoveryRequired):
        transactions.finish_transaction(result['transaction_id'], tmp_path, PATHS, passed=False)
    assert (tmp_path / PATHS[0]).read_bytes() == first_applied
    assert (tmp_path / PATHS[1]).read_bytes() == b'external change'
    with pytest.raises(transactions.RecoveryRequired):
        execute(tmp_path, task, context, edits)


@pytest.mark.parametrize('passed', [True, False, 'launch_error'])
def test_graph_persists_all_checkpoints_and_validates_or_restores_set(tmp_path, passed):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command
    from governance import audit_logger
    import sqlite3
    import main

    task, context, edits, originals = setup_task(tmp_path)
    plan = json.dumps({'tasks': [{key: value for key, value in task.items() if key != 'status'}]})
    analysis = Mock(invoke=Mock(side_effect=[requirements_response('API requirement'), ModelResponse(plan)]))
    edit_model = Mock(invoke=Mock(return_value=ModelResponse(json.dumps({'edits': edits}))))
    state = {'workflow_id': 'multifile-graph', 'workspace': str(tmp_path), 'requirement': 'Coordinated API',
             'implementation_changed': False, 'repair_applied': False}
    config = {'configurable': {'thread_id': 'multifile-graph'}}
    result = {'passed': passed is True, 'exit_code': 0 if passed is True else 1, 'duration_seconds': 0.1, 'output': 'Maven result'}
    with patch('main.model', analysis), patch('main.analyze_codebase', return_value='Service and controller'), patch.object(
        implementation_agent, 'model', edit_model
    ), patch('main.validate_implementation', side_effect=OSError('Maven unavailable') if passed == 'launch_error' else None,
             return_value=result) as validate, patch.object(audit_logger, 'DB_PATH', tmp_path / 'audit.db'), SqliteSaver.from_conn_string(str(tmp_path / 'workflow.db')) as saver:
        app = main.workflow.compile(checkpointer=saver)
        assert '__interrupt__' in app.invoke(state, config=config)
        edit_model.invoke.assert_not_called()
        assert_originals(tmp_path, originals)
        final = app.invoke(Command(resume='approve'), config=config)
        validate.assert_called_once_with(tmp_path)
        assert len(final['implementation_transaction']['checkpoints']) == 2
        assert len(app.get_state(config).values['implementation_transaction']['checkpoints']) == 2
    with sqlite3.connect(tmp_path / 'audit.db') as conn:
        audit = [(event, json.loads(details)) for event, details in conn.execute('SELECT event,details FROM audit_events ORDER BY id')]
    checkpoints = [details for event, details in audit if event == 'FILE_CHECKPOINT_CREATED']
    assert {cp['path'] for cp in checkpoints} == set(PATHS)
    assert all(cp['checkpoint_id'] and cp['original_hash'] == file_hash(originals[cp['path']]) for cp in checkpoints)
    if passed is True:
        assert final['status'] == 'AWAITING_FINAL_HUMAN_REVIEW'
        assert final['source_files_changed'] == 2
        assert all(b'newValue = 2' in (tmp_path / relative).read_bytes() for relative in PATHS)
    else:
        assert final['status'] == 'STOPPED_ROLLED_BACK'
        assert final['source_files_changed'] == 0
        assert_originals(tmp_path, originals)
        assert 'AWAITING_FINAL_HUMAN_REVIEW' not in [event for event, _ in audit]
        assert any(event == 'TRANSACTION_ROLLED_BACK' and set(details['paths']) == set(PATHS) for event, details in audit)


def test_checkpoint_tampering_stops_rollback_before_any_restore(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    result = execute(tmp_path, task, context, edits)
    applied = {relative: (tmp_path / relative).read_bytes() for relative in PATHS}
    journal = tmp_path / '.agent-transactions' / (result['transaction_id'] + '.json')
    record = json.loads(journal.read_text())
    record['files'][1]['original_hash'] = 'tampered'
    journal.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='integrity'):
        transactions.finish_transaction(result['transaction_id'], tmp_path, PATHS, passed=False)
    for relative in PATHS:
        assert (tmp_path / relative).read_bytes() == applied[relative]


def test_staging_failure_writes_no_source(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    real_stage = transactions.stage_file
    calls = 0
    def stage(target, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('Cannot stage second source')
        return real_stage(target, content)
    with patch.object(transactions, 'stage_file', side_effect=stage):
        with pytest.raises(OSError):
            execute(tmp_path, task, context, edits)
    assert_originals(tmp_path, originals)
    assert not list(tmp_path.rglob('.agent-edit-*'))


def test_recovery_journal_can_restore_interrupted_application(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    result = execute(tmp_path, task, context, edits)
    # Reproduce a crash after only one rename. The journal retains both originals.
    (tmp_path / PATHS[1]).write_bytes(originals[PATHS[1]])
    journal = tmp_path / '.agent-transactions' / (result['transaction_id'] + '.json')
    record = json.loads(journal.read_text())
    record['status'] = 'applying'
    journal.write_text(json.dumps(record))
    with pytest.raises(transactions.RecoveryRequired):
        transactions.ensure_workspace_ready(tmp_path)
    transactions.finish_transaction(result['transaction_id'], tmp_path, PATHS, passed=False)
    assert_originals(tmp_path, originals)
    transactions.ensure_workspace_ready(tmp_path)


def test_unapproved_multifile_task_never_generates_or_writes(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    context['approval'] = 'reject'
    with patch.object(implementation_agent, 'model') as model:
        with pytest.raises(PermissionError):
            implementation_agent.implement_task(task, tmp_path, context)
        model.invoke.assert_not_called()
    assert_originals(tmp_path, originals)


def test_multiple_nonoverlapping_edits_use_original_offsets_and_one_checkpoint_per_file(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    edits.insert(0, {'path': PATHS[0], 'old_text': 'package url_shortener;',
                     'new_text': 'package url_shortener;\nimport java.util.Map;'})
    result = execute(tmp_path, task, context, edits)
    assert len(result['checkpoints']) == 2
    assert b'import java.util.Map;' in (tmp_path / PATHS[0]).read_bytes()
    assert b'newValue = 2' in (tmp_path / PATHS[0]).read_bytes()
    transactions.finish_transaction(result['transaction_id'], tmp_path, PATHS, passed=False)
    assert_originals(tmp_path, originals)


def test_replacements_cannot_match_text_introduced_by_another_edit(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    edits.append({'path': PATHS[0], 'old_text': 'newValue = 2', 'new_text': 'thirdValue = 3'})
    with pytest.raises(ValueError):
        execute(tmp_path, task, context, edits)
    assert_originals(tmp_path, originals)


def test_retry_context_uses_recorded_proposal_and_real_diagnostics(tmp_path):
    task, context, edits, originals = setup_task(tmp_path)
    context['previous_implementation_proposal'] = {'edits': edits}
    context['previous_validation_result'] = {'passed': False, 'exit_code': 1,
                                            'output': '[ERROR] incompatible assignment type'}
    model = Mock(invoke=Mock(return_value=ModelResponse(json.dumps({'edits': edits}))))
    with patch.object(implementation_agent, 'model', model):
        result = implementation_agent.implement_task(task, tmp_path, context)
    prompt = model.invoke.call_args.args[0]
    assert 'incompatible assignment type' in prompt
    assert 'Previous failing edit proposal' in prompt
    assert task['description'] in prompt
    transactions.finish_transaction(result['transaction_id'], tmp_path, PATHS, passed=False)
    assert_originals(tmp_path, originals)
