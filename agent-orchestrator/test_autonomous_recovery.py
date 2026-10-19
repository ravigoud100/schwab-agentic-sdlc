from test_support import requirements_response
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock, patch

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agents import implementation_agent
from governance import audit_logger
from governance.failure_context import classify_failure
from llm.provider import ModelResponse
import main

PATHS = [f'url-shortener/src/main/java/url_shortener/{name}.java' for name in ('UrlService', 'UrlController')]


def scenario(tmp_path):
    originals = {}
    for relative in PATHS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        originals[relative] = ('class ' + Path(relative).stem + ' { int value = 1; }\n').encode()
        target.write_bytes(originals[relative])
    task = {'task_id': 'coordinated', 'description': 'Implement approved API behavior', 'dependencies': [],
            'allowed_files': PATHS, 'execution_type': 'implementation'}
    good = {'edits': [{'path': path, 'old_text': 'value = 1', 'new_text': 'value = 2'} for path in PATHS]}
    bad = {'edits': [{'path': path, 'old_text': 'value = 1', 'new_text': 'value = ;'} for path in PATHS]}
    return task, good, bad, originals


def plan_response(task, material=False):
    task = {**task}
    if material:
        task['description'] += '; correct invalid Java initializer using Maven diagnostics'
    return ModelResponse(json.dumps({'tasks': [task], 'change_summary': 'Target invalid initializer',
                                    'correction_strategy': 'Replace the invalid initializer while preserving the approved API.'}))


def validation(passed):
    return {'passed': passed, 'exit_code': 0 if passed else 1, 'duration_seconds': 0.1,
            'output': 'BUILD SUCCESS' if passed else '[ERROR] COMPILATION ERROR: illegal start of expression\n[ERROR] Compilation failure'}


@pytest.mark.parametrize('material,decision', [(False, 'approve'), (True, 'approve'), (True, 'reject')])
def test_single_workflow_recovers_with_evidence_and_required_approval(tmp_path, material, decision):
    task, good, bad, originals = scenario(tmp_path)
    model = Mock(invoke=Mock(side_effect=[requirements_response('API requirement'), ModelResponse(json.dumps({'tasks': [task]})),
                                         plan_response(task, material)]))
    implementation = Mock(invoke=Mock(side_effect=[ModelResponse(json.dumps(bad)), ModelResponse(json.dumps(good))]))
    config = {'configurable': {'thread_id': 'recovery-test'}}
    initial = {'workflow_id': 'recovery-test', 'workspace': str(tmp_path), 'requirement': 'Coordinated API enhancement',
               'implementation_changed': False, 'repair_applied': False, 'retry_count': 0, 'legacy': True}
    with patch('main.model', model), patch('main.analyze_codebase', return_value='Actual code'), patch.object(
        implementation_agent, 'model', implementation
    ), patch('main.validate_implementation', side_effect=[validation(False), validation(True)]) as validate, patch.object(
        audit_logger, 'DB_PATH', tmp_path / 'audit.db'
    ), SqliteSaver.from_conn_string(str(tmp_path / 'workflow.db')) as saver:
        app = main.workflow.compile(checkpointer=saver)
        assert '__interrupt__' in app.invoke(initial, config=config)
        implementation.invoke.assert_not_called()
        result = app.invoke(Command(resume='approve'), config=config)
        if material:
            assert result['__interrupt__'][0].value['type'] == 'RECOVERY_PLAN_APPROVAL'
            assert implementation.invoke.call_count == 1
            for path, original in originals.items():
                assert (tmp_path/path).read_bytes() == original
            result = app.invoke(Command(resume=decision), config=config)
        if decision == 'reject':
            assert result['status'] == 'STOPPED_RECOVERY_REJECTED'
            assert implementation.invoke.call_count == 1
            assert validate.call_count == 1
        else:
            assert result['status'] == 'AWAITING_FINAL_HUMAN_REVIEW'
            assert result['retry_count'] == 1
            assert validate.call_count == 2
            assert implementation.invoke.call_count == 2
            assert all(b'value = 2' in (tmp_path/path).read_bytes() for path in PATHS)
            retry_prompt = implementation.invoke.call_args_list[1].args[0]
            assert 'illegal start of expression' in retry_prompt
            assert 'value = ;' in retry_prompt
        failure = result['attempt_history'][0]
        assert failure['category'] == 'java_compilation'
        assert failure['attempted_edits'] == bad
        assert failure['original_hashes'] == failure['restored_hashes']
        assert failure['rolled_back'] is True
        replan_prompt = model.invoke.call_args_list[2].args[0]
        assert failure['evidence_hash'] in replan_prompt
        assert 'illegal start of expression' in replan_prompt
        assert result['plan_version'] == 2
        assert app.get_state(config).values['status'] == result['status']
    with sqlite3.connect(tmp_path/'audit.db') as conn:
        events = [(event,json.loads(details)) for event,details in conn.execute('SELECT event,details FROM audit_events ORDER BY id')]
    names = [event for event,_ in events]
    assert 'FAILURE_CLASSIFIED' in names and 'DIAGNOSTICS_COLLECTED' in names
    assert names.index('TRANSACTION_ROLLED_BACK') < names.index('RECOVERY_PLAN_CREATED')
    assert names.count('IMPLEMENTATION_ATTEMPT_STARTED') == (1 if decision == 'reject' else 2)
    if decision == 'reject':
        assert 'AWAITING_FINAL_HUMAN_REVIEW' not in names
    else:
        approval = next(details for event,details in events if event == 'RECOVERY_PLAN_APPROVED')
        assert approval['approval_basis'] == ('human' if material else 'unchanged_approved_tasks')


def test_invalid_json_is_diagnosed_and_corrected_before_any_maven_failure(tmp_path):
    task, good, _, originals = scenario(tmp_path)
    model = Mock(invoke=Mock(side_effect=[requirements_response('Requirement'), ModelResponse(json.dumps({'tasks':[task]})),plan_response(task)]))
    implementation = Mock(invoke=Mock(side_effect=[ModelResponse('```broken JSON'), ModelResponse(json.dumps(good))]))
    with patch('main.model',model),patch('main.analyze_codebase',return_value='Code'),patch.object(
        implementation_agent,'model',implementation),patch('main.validate_implementation',return_value=validation(True)) as validate,patch(
        'main.log_event'),patch('governance.file_transaction.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        config={'configurable':{'thread_id':'invalid-json'}}
        app.invoke({'workflow_id':'invalid-json','workspace':str(tmp_path),'requirement':'API','legacy':True},config=config)
        result=app.invoke(Command(resume='approve'),config=config)
    assert result['status']=='AWAITING_FINAL_HUMAN_REVIEW'
    assert result['failure_context']['category']=='model_output'
    assert result['failure_context']['raw_response']=='```broken JSON'
    assert validate.call_count==1
    assert '```broken JSON' in model.invoke.call_args_list[2].args[0]


def test_retry_exhaustion_restores_every_attempt_and_stops(tmp_path):
    task, good, bad, originals=scenario(tmp_path)
    model=Mock(invoke=Mock(side_effect=[requirements_response('Requirement'),ModelResponse(json.dumps({'tasks':[task]})),plan_response(task),plan_response(task)]))
    implementation=Mock(invoke=Mock(return_value=ModelResponse(json.dumps(bad))))
    with patch('main.model',model),patch('main.analyze_codebase',return_value='Code'),patch.object(
        implementation_agent,'model',implementation),patch('main.validate_implementation',return_value=validation(False)),patch(
        'main.log_event'),patch('governance.file_transaction.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        config={'configurable':{'thread_id':'exhaustion'}}
        app.invoke({'workflow_id':'exhaustion','workspace':str(tmp_path),'requirement':'API','legacy':True},config=config)
        result=app.invoke(Command(resume='approve'),config=config)
    assert result['status']=='STOPPED_ROLLED_BACK'
    assert result['recovery_stop_reason']=='RETRY_LIMIT_EXHAUSTED'
    assert result['retry_count']==main.MAX_RETRIES
    assert implementation.invoke.call_count==main.MAX_RETRIES+1
    assert len(result['attempt_history'])==main.MAX_RETRIES+1
    for path,original in originals.items(): assert (tmp_path/path).read_bytes()==original


@pytest.mark.parametrize('error_type,message,result,expected,retryable',[
    ('JSONDecodeError','Invalid JSON',None,'model_output',True),
    ('ValueError','Expected source text must occur exactly once',None,'source_edit_conflict',True),
    ('RuntimeError','Source changed during generation',None,'source_edit_conflict',False),
    ('PermissionError','Unauthorized path',None,'authorization',False),
    ('','',validation(False),'java_compilation',True),
    ('','',{'exit_code':1,'output':'[ERROR] Failures: expected true but was false'},'test_assertion',True),
    ('','',{'exit_code':1,'output':'Could not resolve dependency'},'infrastructure',False),
    ('','',{'exit_code':-1,'output':'Maven timed out'},'infrastructure',False),
])
def test_classification_is_deterministic(error_type,message,result,expected,retryable):
    assert classify_failure(error_type,message,result)==(expected,retryable)


def test_infrastructure_failure_restores_files_without_replanning(tmp_path):
    task,good,bad,originals=scenario(tmp_path)
    model=Mock(invoke=Mock(side_effect=[requirements_response('Requirement'),ModelResponse(json.dumps({'tasks':[task]}))]))
    with patch('main.model',model),patch('main.analyze_codebase',return_value='Code'),patch.object(
        implementation_agent,'model',Mock(invoke=Mock(return_value=ModelResponse(json.dumps(good))))),patch(
        'main.validate_implementation',side_effect=OSError('Maven is unavailable')),patch('main.log_event'),patch(
        'governance.file_transaction.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        config={'configurable':{'thread_id':'infrastructure'}}
        app.invoke({'workflow_id':'infrastructure','workspace':str(tmp_path),'requirement':'API','legacy':True},config=config)
        result=app.invoke(Command(resume='approve'),config=config)
    assert result['status']=='STOPPED_ROLLED_BACK'
    assert result['failure_context']['category']=='infrastructure'
    assert result['recovery_stop_reason']=='NONRECOVERABLE_INFRASTRUCTURE'
    assert model.invoke.call_count==2
    for path,original in originals.items(): assert (tmp_path/path).read_bytes()==original


def test_unexpected_source_hash_blocks_rollback_and_all_corrections(tmp_path):
    task,good,bad,originals=scenario(tmp_path)
    model=Mock(invoke=Mock(side_effect=[requirements_response('Requirement'),ModelResponse(json.dumps({'tasks':[task]}))]))
    def external_change(workspace):
        (tmp_path/PATHS[1]).write_bytes(b'external user change')
        return validation(False)
    implementation=Mock(invoke=Mock(return_value=ModelResponse(json.dumps(good))))
    with patch('main.model',model),patch('main.analyze_codebase',return_value='Code'),patch.object(
        implementation_agent,'model',implementation),patch('main.validate_implementation',side_effect=external_change),patch(
        'main.log_event'),patch('governance.file_transaction.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        config={'configurable':{'thread_id':'conflict'}}
        app.invoke({'workflow_id':'conflict','workspace':str(tmp_path),'requirement':'API','legacy':True},config=config)
        result=app.invoke(Command(resume='approve'),config=config)
    assert result['status']=='STOPPED_ROLLBACK_REQUIRES_REVIEW'
    assert result['recovery_stop_reason']=='ROLLBACK_BLOCKED'
    assert result['failure_context']['category']=='source_edit_conflict'
    assert result['failure_context']['recoverable'] is False
    assert (tmp_path/PATHS[1]).read_bytes()==b'external user change'
    assert b'value = 2' in (tmp_path/PATHS[0]).read_bytes()
    assert implementation.invoke.call_count==1
    assert model.invoke.call_count==2


def test_recovery_plan_cannot_bypass_generation_via_execution_type(tmp_path):
    task,good,bad,originals=scenario(tmp_path)
    invalid={**task,'execution_type':'validate_only'}
    model=Mock(invoke=Mock(side_effect=[requirements_response('Requirement'),ModelResponse(json.dumps({'tasks':[task]})),plan_response(invalid)]))
    with patch('main.model',model),patch('main.analyze_codebase',return_value='Code'),patch.object(
        implementation_agent,'model',Mock(invoke=Mock(return_value=ModelResponse(json.dumps(bad))))),patch(
        'main.validate_implementation',return_value=validation(False)),patch('main.log_event'),patch(
        'governance.file_transaction.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        config={'configurable':{'thread_id':'invalid-plan'}}
        app.invoke({'workflow_id':'invalid-plan','workspace':str(tmp_path),'requirement':'API','legacy':True},config=config)
        result=app.invoke(Command(resume='approve'),config=config)
    assert result['status']=='STOPPED_INVALID_RECOVERY_PLAN'
    for path,original in originals.items(): assert (tmp_path/path).read_bytes()==original
