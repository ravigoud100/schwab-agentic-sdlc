"""Controlled model/approval/Maven fixtures; no live plan approval is implied."""
import json
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.parallel_execution import build_workflow, check_context, MAX_ATTEMPTS
from governance.task_orchestrator import task_definition_hash
from llm.provider import ModelResponse

A = 'url-shortener/src/main/java/url_shortener/A.java'
B = 'url-shortener/src/main/java/url_shortener/B.java'
TEST = 'url-shortener/src/test/java/url_shortener/OriginalTest.java'


def setup(tmp_path, monkeypatch):
    root = tmp_path/'workspace'
    for p, text in [(A, 'class A { int value = 1; }'), (B, 'class B { int value = 1; }'),
                    (TEST, 'original assertions'), ('url-shortener/pom.xml', 'original build')]:
        target = root/p; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(text)
    tasks = [{'task_id': name, 'description': 'Increase '+name, 'dependencies': [], 'status': 'PENDING',
              'execution_type': 'implementation', 'allowed_files': [path]} for name, path in [('a', A), ('b', B)]]
    context = {'workflow_id': 'parallel-test', 'workspace': str(root), 'requirement': 'Increase both counters',
               'approval': 'approve', 'tasks': tasks, 'approved_task_hash': task_definition_hash(tasks),
               'plan': json.dumps({'tasks': tasks})}
    monkeypatch.setattr(audit_logger, 'DB_PATH', tmp_path/'audit.db')
    return {'context': context, 'evidence': str(tmp_path/'evidence'), 'cache': str(tmp_path/'cache')}


def audit():
    import sqlite3
    with sqlite3.connect(audit_logger.DB_PATH) as conn:
        return [(e, json.loads(d)) for e, d in conn.execute('select event,details from audit_events order by id')]


class Provider:
    def __init__(self, barrier=None, fail=None):
        self.barrier = barrier; self.fail = fail; self.calls = []; self.lock = threading.Lock()
    def invoke(self, prompt, **kwargs):
        task = json.loads(prompt.split('APPROVED TASK (filtered production scope):\n')[1].split('\nORIGINAL COMPLETE SOURCES')[0])
        path = task['allowed_files'][0]
        with self.lock:self.calls.append(path)
        if self.barrier:self.barrier.wait(timeout=5)
        if self.fail == path:return ModelResponse('not json')
        return ModelResponse(json.dumps({'files': [{'path': path, 'content': 'class '+Path(path).stem+' { int value = 2; }'}]}))


def validator(workspace, evidence, label, cache):
    return {'exit_code': 0, 'cases': {'OriginalTest#behavior': 'passed'}, 'command': ['synthetic']}


def approve(app, state, config):
    result = app.invoke(state, config)
    for _ in range(2):
        assert app.get_state(config).next == ('review',)
        result = app.invoke(Command(resume='approve'), config)
    return result


def test_workers_run_concurrently_and_join_before_application_and_validation(tmp_path, monkeypatch):
    state = setup(tmp_path, monkeypatch); model = Provider(threading.Barrier(2)); calls = []
    def gate(root, evidence, label, cache):
        calls.append(label)
        events = [e for e, d in audit()]
        if label == 'baseline':assert not model.calls
        else:
            assert events.count('PARALLEL_TASK_PREPARED') == 2
            assert events.index('PARALLEL_SYNCHRONIZED') < events.index('TRANSACTION_APPLIED') < events.index('PARALLEL_VALIDATION_STARTED')
            assert all('value = 2' in (root/p).read_text() for p in [A, B])
        return validator(root, evidence, label, cache)
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app = build_workflow(model, validator=gate).compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'parallel-test'}}
        result = approve(app, state, config)
        assert not app.get_state(config).next
        assert len(list(saver.list(config))) >= 7
    assert result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert calls == ['baseline', 'combined'] and sorted(model.calls) == [A, B]
    assert set(result['task_checkpoints']) == {'a', 'b'}
    for checkpoint in result['task_checkpoints'].values():assert checkpoint['transaction_id'] == result['transaction']['transaction_id']
    root = Path(state['context']['workspace'])
    assert (root/TEST).read_text() == 'original assertions'
    assert (root/'url-shortener/pom.xml').read_text() == 'original build'


@pytest.mark.parametrize('kind', ['overlap', 'traversal', 'symlink', 'hardlink', 'test', 'build', 'circular', 'validate_only', 'unapproved', 'stale', 'sequential_receipt'])
def test_conflicts_and_unapproved_scopes_fail_before_model_or_maven(tmp_path, monkeypatch, kind):
    state = setup(tmp_path, monkeypatch); c = state['context']; tasks = c['tasks']; root = Path(c['workspace'])
    if kind == 'overlap':tasks[1]['allowed_files'] = [A]
    elif kind == 'traversal':tasks[1]['allowed_files'] = ['../B.java']
    elif kind == 'symlink':(root/B).unlink(); (root/B).symlink_to(root/A)
    elif kind == 'hardlink':(root/B).unlink(); (root/B).hardlink_to(root/A)
    elif kind == 'test':tasks[1]['allowed_files'] = [TEST]
    elif kind == 'build':tasks[1]['allowed_files'] = ['url-shortener/pom.xml']
    elif kind == 'circular':tasks[0]['dependencies'] = ['b']; tasks[1]['dependencies'] = ['a']
    elif kind == 'validate_only':tasks[1]['execution_type'] = 'validate_only'
    elif kind == 'unapproved':c['approval'] = 'reject'
    elif kind == 'stale':tasks[1]['description'] = 'unreviewed'
    elif kind == 'sequential_receipt':c['plan_lineage_required'] = True
    if kind not in {'stale', 'traversal', 'circular'}:c['approved_task_hash'] = task_definition_hash(tasks)
    model = Mock(); gate = Mock()
    result = build_workflow(model, validator=gate).compile().invoke(state)
    assert result['status'] == 'STOPPED_PARALLEL_ENTRY'
    model.invoke.assert_not_called(); gate.assert_not_called()


def test_rejected_task_prevents_all_execution(tmp_path, monkeypatch):
    state = setup(tmp_path, monkeypatch); model = Mock(); gate = Mock()
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app = build_workflow(model, validator=gate).compile(checkpointer=saver); config = {'configurable': {'thread_id': 'x'}}
        app.invoke(state, config); app.invoke(Command(resume='approve'), config)
        result = app.invoke(Command(resume='reject'), config)
    assert result['status'] == 'STOPPED_PARALLEL_REJECTED'; model.invoke.assert_not_called(); gate.assert_not_called()


def test_failed_worker_is_bounded_and_never_applies_successful_sibling(tmp_path, monkeypatch):
    state = setup(tmp_path, monkeypatch); model = Provider(fail=B); calls = []
    def gate(*args):calls.append(args[2]); return validator(*args)
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        result = approve(build_workflow(model, validator=gate).compile(checkpointer=saver), state, {'configurable': {'thread_id': 'x'}})
    assert result['status'] == 'STOPPED_PARALLEL_WORKER_FAILED'
    assert model.calls.count(B) == MAX_ATTEMPTS and model.calls.count(A) == 1
    assert calls == ['baseline']
    assert not any(e == 'TRANSACTION_APPLIED' for e, _ in audit())
    assert all('value = 1' in (Path(state['context']['workspace'])/p).read_text() for p in [A, B])


@pytest.mark.parametrize('failure', ['test', 'compile', 'launch', 'skip', 'missing', 'protected', 'external'])
def test_combined_failure_restores_set_without_overwriting_external_edits(tmp_path, monkeypatch, failure):
    state = setup(tmp_path, monkeypatch); root = Path(state['context']['workspace'])
    def gate(workspace, evidence, label, cache):
        record = validator(workspace, evidence, label, cache)
        if label == 'baseline':return record
        if failure == 'launch':raise OSError('Maven unavailable')
        if failure == 'external':(root/A).write_text('external source')
        if failure == 'protected':(root/TEST).write_text('external test')
        if failure == 'skip':record['cases']['OriginalTest#behavior'] = 'skipped'
        if failure == 'missing':record['cases'] = {}
        if failure in {'compile', 'test'}:record['exit_code'] = 1
        return record
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        result = approve(build_workflow(Provider(), validator=gate).compile(checkpointer=saver), state, {'configurable': {'thread_id': 'x'}})
    assert result['status'] == ('STOPPED_PARALLEL_RECOVERY_REQUIRED' if failure == 'external' else 'STOPPED_PARALLEL_ROLLED_BACK')
    if failure == 'external':
        assert (root/A).read_text() == 'external source'
        # Whole-set preflight prevents partial compensation of B.
        assert 'value = 2' in (root/B).read_text()
    else:assert all('value = 1' in (root/p).read_text() for p in [A, B])


def test_approval_resume_in_new_process_style_does_not_repeat_source_writes(tmp_path, monkeypatch):
    state = setup(tmp_path, monkeypatch); db = str(tmp_path/'workflow.db'); config = {'configurable': {'thread_id': 'x'}}; model = Provider()
    with SqliteSaver.from_conn_string(db) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver)
        app.invoke(state, config); app.invoke(Command(resume='approve'), config)
    with SqliteSaver.from_conn_string(db) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver)
        result = app.invoke(Command(resume='approve'), config)
    assert result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert sum(e == 'PARALLEL_TASK_APPROVED' for e, _ in audit()) == 2
    assert sum(e == 'TRANSACTION_APPLIED' for e, _ in audit()) == 1


def test_invalid_proposal_regenerates_with_diagnostics_without_repeating_sibling(tmp_path, monkeypatch):
    state = setup(tmp_path, monkeypatch)
    class Regenerating(Provider):
        def invoke(self, prompt, **kwargs):
            if '"task_id": "b"' in prompt.split('APPROVED TASK (filtered production scope):')[1].split('ORIGINAL COMPLETE SOURCES')[0]:
                if B not in self.calls:
                    self.calls.append(B); return ModelResponse('not json')
                assert 'JSONDecodeError' in prompt and 'not json' in prompt
            return super().invoke(prompt, **kwargs)
    model = Regenerating()
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        result = approve(build_workflow(model, validator=validator).compile(checkpointer=saver), state, {'configurable': {'thread_id': 'x'}})
    assert result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert model.calls.count(A) == 1 and model.calls.count(B) == 2


@pytest.mark.parametrize('mutation', ['source', 'baseline', 'plan', 'requirement'])
def test_changed_evidence_at_task_review_blocks_all_workers(tmp_path, monkeypatch, mutation):
    state = setup(tmp_path, monkeypatch); model = Mock(); gate = Mock(); config = {'configurable': {'thread_id': 'x'}}
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app = build_workflow(model, validator=gate).compile(checkpointer=saver)
        app.invoke(state, config); app.invoke(Command(resume='approve'), config)
        if mutation == 'source':(Path(state['context']['workspace'])/A).write_text('external source')
        elif mutation == 'baseline':(Path(state['evidence'])/'originals/baseline.json').write_text('{}')
        elif mutation in {'plan', 'requirement'}:
            c = dict(app.get_state(config).values['context']); c['plan' if mutation == 'plan' else 'requirements_hash'] = 'changed'
            app.update_state(config, {'context': c})
        result = app.invoke(Command(resume='approve'), config)
    assert result['status'] == 'STOPPED_PARALLEL_APPROVAL_INVALID'
    model.invoke.assert_not_called(); gate.assert_not_called()


@pytest.mark.parametrize('mutation', ['receipt', 'outcome', 'external_source'])
def test_join_rechecks_approvals_proposals_and_originals_before_first_write(tmp_path, monkeypatch, mutation):
    state = setup(tmp_path, monkeypatch); config = {'configurable': {'thread_id': 'x'}}
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app = build_workflow(Provider(), validator=validator).compile(checkpointer=saver, interrupt_before=['join'])
        approve(app, state, config)
        assert app.get_state(config).next == ('join',)
        if mutation == 'receipt':
            import sqlite3
            with sqlite3.connect(audit_logger.DB_PATH) as conn:conn.execute("update audit_events set details='{}' where event='PARALLEL_TASK_APPROVED'")
        elif mutation == 'outcome':
            bad = dict(app.get_state(config).values['outcomes'][0]); bad['task_id'] = 'unknown'
            app.update_state(config, {'outcomes': [bad]}, as_node='worker')
        else:(Path(state['context']['workspace'])/A).write_text('external source')
        result = app.invoke(None, config)
    assert result['status'] == 'STOPPED_PARALLEL_FAILED'
    assert not any(e == 'TRANSACTION_APPLIED' for e, _ in audit())


def test_tampered_transaction_journal_stops_without_overwriting_external_data(tmp_path, monkeypatch):
    state = setup(tmp_path, monkeypatch); config = {'configurable': {'thread_id': 'x'}}
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app = build_workflow(Provider(), validator=validator).compile(checkpointer=saver, interrupt_before=['validation'])
        result = approve(app, state, config)
        root = Path(state['context']['workspace']); tx = result['transaction']['transaction_id']
        journal = root/'.agent-transactions'/(tx+'.json'); record = json.loads(journal.read_text())
        record['files'][0]['original_hash'] = 'tampered'; journal.write_text(json.dumps(record))
        result = app.invoke(None, config)
    assert result['status'] == 'STOPPED_PARALLEL_RECOVERY_REQUIRED'
    assert not any(e == 'PARALLEL_VALIDATION_STARTED' for e, _ in audit())


def test_checkpointed_workers_resume_at_join_without_regeneration(tmp_path, monkeypatch):
    state = setup(tmp_path, monkeypatch); config = {'configurable': {'thread_id': 'x'}}; db = str(tmp_path/'workflow.db')
    model = Provider(threading.Barrier(2))
    with SqliteSaver.from_conn_string(db) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver, interrupt_before=['join'])
        result = approve(app, state, config)
        assert len(result['outcomes']) == 2
        assert not any(e == 'TRANSACTION_APPLIED' for e, _ in audit())
    with SqliteSaver.from_conn_string(db) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver)
        result = app.invoke(None, config)
    assert result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW' and len(model.calls) == 2
    assert sum(e == 'TRANSACTION_APPLIED' for e, _ in audit()) == 1


def test_unauthorized_model_scope_stops_without_regeneration_or_writes(tmp_path, monkeypatch):
    state = setup(tmp_path, monkeypatch)
    class Malicious(Provider):
        def invoke(self, prompt, **kwargs):
            response = super().invoke(prompt, **kwargs)
            payload = json.loads(response.text)
            payload['files'][0]['path'] = TEST
            return ModelResponse(json.dumps(payload))
    model = Malicious()
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        result = approve(build_workflow(model, validator=validator).compile(checkpointer=saver), state, {'configurable': {'thread_id': 'x'}})
    assert result['status'] == 'STOPPED_PARALLEL_WORKER_FAILED' and len(model.calls) == 2
    assert all(o['attempts'] == 1 for o in result['outcomes'])
    assert not any(e == 'TRANSACTION_APPLIED' for e, _ in audit())


def test_cli_rejects_parallel_receipt_downgrade_before_initialization(tmp_path):
    import main
    with pytest.raises(ValueError, match='Sequential|sequential'):
        main.main(obligation_contract={'example': True}, design_required=True, plan_lineage_required=True,
                  parallel_evidence=tmp_path/'evidence', integration_cache=tmp_path/'cache')


def test_removed_approval_during_validation_cannot_mark_transaction_validated(tmp_path, monkeypatch):
    state = setup(tmp_path, monkeypatch)
    def gate(root, evidence, label, cache):
        if label == 'combined':
            import sqlite3
            with sqlite3.connect(audit_logger.DB_PATH) as conn:conn.execute("delete from audit_events where event='PARALLEL_TASK_APPROVED'")
        return validator(root, evidence, label, cache)
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        result = approve(build_workflow(Provider(), validator=gate).compile(checkpointer=saver), state, {'configurable': {'thread_id': 'x'}})
    assert result['status'] == 'STOPPED_PARALLEL_ROLLED_BACK'
    assert not any(e == 'PARALLEL_VALIDATION_PASSED' for e, _ in audit())


def test_interrupted_worker_evidence_is_preserved_without_budget_reset(tmp_path, monkeypatch):
    from governance.semantic_plan import digest
    state = setup(tmp_path, monkeypatch); model = Provider(); config = {'configurable': {'thread_id': 'x'}}
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver)
        app.invoke(state, config); app.invoke(Command(resume='approve'), config)
        directory = Path(state['evidence'])/('task-'+digest('a')); directory.mkdir()
        retained = directory/'1-response.txt'; retained.write_text('retained interrupted attempt')
        result = app.invoke(Command(resume='approve'), config)
    assert result['status'] == 'STOPPED_PARALLEL_WORKER_FAILED'
    assert model.calls == [B] and retained.read_text() == 'retained interrupted attempt'
    assert not any(e == 'TRANSACTION_APPLIED' for e, _ in audit())


def test_main_parallel_wrapper_keeps_nested_task_approvals_and_shared_thread(tmp_path, monkeypatch):
    import main
    from governance import parallel_execution
    from langgraph.graph import StateGraph, START, END
    state = setup(tmp_path, monkeypatch); model = Provider(); factory = parallel_execution.build_workflow
    monkeypatch.setattr(main, 'model', model)
    monkeypatch.setattr(parallel_execution, 'build_workflow', lambda provider: factory(provider, validator=validator))
    root = StateGraph(main.EngineeringState)
    root.add_node('parallel_execution', main.parallel_node)
    root.add_edge(START, 'parallel_execution'); root.add_edge('parallel_execution', END)
    initial = {**state['context'], 'parallel_evidence': state['evidence'], 'integration_cache': state['cache']}
    config = {'configurable': {'thread_id': 'nested-parallel'}}
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app = root.compile(checkpointer=saver)
        app.invoke(initial, config)
        for _ in range(2):
            snapshot = app.get_state(config)
            assert snapshot.tasks[0].interrupts[0].value['type'] == 'PARALLEL_TASK_APPROVAL'
            result = app.invoke(Command(resume='approve'), config)
        assert not app.get_state(config).next
    assert result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert len(result['parallel_result']['task_checkpoints']) == 2
    assert len(model.calls) == 2
    assert sum(e == 'TRANSACTION_APPLIED' for e, _ in audit()) == 1


C = 'url-shortener/src/main/java/url_shortener/C.java'


def setup_two_wave(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    for p, text in [
        (A, 'class A { int value = 1; }'),
        (B, 'class B { int value = 1; }'),
        (C, 'class C { int value = 1; }'),
        (TEST, 'original assertions'),
        ('url-shortener/pom.xml', 'original build'),
    ]:
        target = root / p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    tasks = [
        {'task_id': 'a', 'description': 'Increase a', 'dependencies': [], 'status': 'PENDING',
         'execution_type': 'implementation', 'allowed_files': [A]},
        {'task_id': 'b', 'description': 'Increase b', 'dependencies': [], 'status': 'PENDING',
         'execution_type': 'implementation', 'allowed_files': [B]},
        {'task_id': 'c', 'description': 'Increase c using a and b', 'dependencies': ['a', 'b'], 'status': 'PENDING',
         'execution_type': 'implementation', 'allowed_files': [C]},
    ]
    context = {
        'workflow_id': 'two-wave-test', 'workspace': str(root), 'requirement': 'Multi-wave counters',
        'approval': 'approve', 'tasks': tasks, 'approved_task_hash': task_definition_hash(tasks),
        'plan': json.dumps({'tasks': tasks}),
    }
    monkeypatch.setattr(audit_logger, 'DB_PATH', tmp_path / 'audit.db')
    return {'context': context, 'evidence': str(tmp_path / 'evidence'), 'cache': str(tmp_path / 'cache')}


def approve_n(app, state, config, n=3):
    result = app.invoke(state, config)
    for _ in range(n):
        assert app.get_state(config).next == ('review',)
        result = app.invoke(Command(resume='approve'), config)
    return result


class WaveProvider(Provider):
    def invoke(self, prompt, **kwargs):
        task = json.loads(prompt.split('APPROVED TASK (filtered production scope):\n')[1].split('\nORIGINAL COMPLETE SOURCES')[0])
        path = task['allowed_files'][0]
        with self.lock:
            self.calls.append(path)
        if self.barrier and path in (A, B):
            self.barrier.wait(timeout=5)
        if self.fail == path:
            return ModelResponse('not json')
        return ModelResponse(json.dumps({'files': [{'path': path, 'content': 'class ' + Path(path).stem + ' { int value = 2; }'}]}))


def test_two_wave_dag_executes_concurrently_then_synchronizes_dependent_wave(tmp_path, monkeypatch):
    state = setup_two_wave(tmp_path, monkeypatch)
    barrier = threading.Barrier(2)
    model = WaveProvider(barrier=barrier)
    calls = []
    root = Path(state['context']['workspace'])

    def gate(ws, evidence, label, cache):
        calls.append(label)
        events = [e for e, _ in audit()]
        if label == 'baseline':
            assert not model.calls
        elif label == 'wave-0':
            assert events.count('PARALLEL_TASK_PREPARED') == 2
            assert (root / A).read_text() == 'class A { int value = 2; }'
            assert (root / B).read_text() == 'class B { int value = 2; }'
            assert (root / C).read_text() == 'class C { int value = 1; }'
        elif label == 'wave-1':
            assert events.count('PARALLEL_TASK_PREPARED') == 3
            assert (root / C).read_text() == 'class C { int value = 2; }'
        return validator(ws, evidence, label, cache)

    with SqliteSaver.from_conn_string(str(tmp_path / 'workflow.db')) as saver:
        app = build_workflow(model, validator=gate).compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'two-wave-success'}}
        result = approve_n(app, state, config, n=3)
        assert not app.get_state(config).next

    assert result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert calls == ['baseline', 'wave-0', 'wave-1']
    assert set(result['task_checkpoints']) == {'a', 'b', 'c'}
    assert len(result['transactions']) == 2
    assert sum(e == 'TRANSACTION_APPLIED' for e, _ in audit()) == 2
    assert (root / TEST).read_text() == 'original assertions'
    assert (root / 'url-shortener/pom.xml').read_text() == 'original build'


def test_dependent_task_receives_verified_predecessor_outputs(tmp_path, monkeypatch):
    state = setup_two_wave(tmp_path, monkeypatch)
    root = Path(state['context']['workspace'])
    predecessor_states_seen = []

    class DependentVerifyingProvider(Provider):
        def invoke(self, prompt, **kwargs):
            if '"task_id": "c"' in prompt:
                a_content = (root / A).read_text()
                b_content = (root / B).read_text()
                predecessor_states_seen.append((a_content, b_content))
                assert 'value = 2' in a_content
                assert 'value = 2' in b_content
                assert 'dependencies' in prompt and '"a"' in prompt and '"b"' in prompt
            return super().invoke(prompt, **kwargs)

    model = DependentVerifyingProvider()
    with SqliteSaver.from_conn_string(str(tmp_path / 'workflow.db')) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'predecessor-verification'}}
        result = approve_n(app, state, config, n=3)

    assert result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert len(predecessor_states_seen) == 1
    assert 'value = 2' in predecessor_states_seen[0][0]
    assert 'value = 2' in predecessor_states_seen[0][1]


def test_failure_in_later_wave_restores_complete_original_workspace(tmp_path, monkeypatch):
    state = setup_two_wave(tmp_path, monkeypatch)
    root = Path(state['context']['workspace'])

    def failing_gate(ws, evidence, label, cache):
        if label == 'wave-1':
            return {'exit_code': 1, 'cases': {'OriginalTest#behavior': 'failed'}, 'command': ['synthetic']}
        return validator(ws, evidence, label, cache)

    with SqliteSaver.from_conn_string(str(tmp_path / 'workflow.db')) as saver:
        app = build_workflow(Provider(), validator=failing_gate).compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'wave1-fail'}}
        result = approve_n(app, state, config, n=3)

    assert result['status'] == 'STOPPED_PARALLEL_ROLLED_BACK'
    assert (root / A).read_text() == 'class A { int value = 1; }'
    assert (root / B).read_text() == 'class B { int value = 1; }'
    assert (root / C).read_text() == 'class C { int value = 1; }'
    rollback_events = [d for e, d in audit() if e == 'PARALLEL_ROLLED_BACK']
    assert len(rollback_events) == 2


def test_worker_failure_in_later_wave_rolls_back_preceding_waves(tmp_path, monkeypatch):
    state = setup_two_wave(tmp_path, monkeypatch)
    root = Path(state['context']['workspace'])
    model = Provider(fail=C)

    with SqliteSaver.from_conn_string(str(tmp_path / 'workflow.db')) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'worker-wave1-fail'}}
        result = approve_n(app, state, config, n=3)

    assert result['status'] == 'STOPPED_PARALLEL_WORKER_FAILED'
    assert model.calls.count(C) == MAX_ATTEMPTS
    assert (root / A).read_text() == 'class A { int value = 1; }'
    assert (root / B).read_text() == 'class B { int value = 1; }'
    assert (root / C).read_text() == 'class C { int value = 1; }'
    rollback_events = [d for e, d in audit() if e == 'PARALLEL_ROLLED_BACK']
    assert len(rollback_events) == 1


def test_rejected_task_in_two_wave_plan_halts_before_execution(tmp_path, monkeypatch):
    state = setup_two_wave(tmp_path, monkeypatch)
    model = Mock(); gate = Mock()
    with SqliteSaver.from_conn_string(str(tmp_path / 'workflow.db')) as saver:
        app = build_workflow(model, validator=gate).compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'reject-third'}}
        app.invoke(state, config)
        app.invoke(Command(resume='approve'), config)
        app.invoke(Command(resume='approve'), config)
        result = app.invoke(Command(resume='reject'), config)

    assert result['status'] == 'STOPPED_PARALLEL_REJECTED'
    model.invoke.assert_not_called()
    gate.assert_not_called()


def test_two_wave_dag_checkpoint_recovery_resumes_without_reexecuting_wave_zero(tmp_path, monkeypatch):
    state = setup_two_wave(tmp_path, monkeypatch)
    db = str(tmp_path / 'workflow.db')
    config = {'configurable': {'thread_id': 'resume-two-wave'}}
    model = Provider()

    with SqliteSaver.from_conn_string(db) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver)
        result = approve_n(app, state, config, n=3)

    assert result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert len(model.calls) == 3

    with SqliteSaver.from_conn_string(db) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver)
        res = app.invoke(None, config)
    assert res['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert len(model.calls) == 3


def test_external_tamper_of_wave_zero_during_wave_one_triggers_recovery_required(tmp_path, monkeypatch):
    state = setup_two_wave(tmp_path, monkeypatch)
    root = Path(state['context']['workspace'])

    def gate(ws, evidence, label, cache):
        if label == 'wave-1':
            (root / A).write_text('external process tamper')
            return {'exit_code': 1, 'cases': {'OriginalTest#behavior': 'failed'}, 'command': ['synthetic']}
        return validator(ws, evidence, label, cache)

    with SqliteSaver.from_conn_string(str(tmp_path / 'workflow.db')) as saver:
        app = build_workflow(Provider(), validator=gate).compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'tamper-wave0'}}
        result = approve_n(app, state, config, n=3)

    assert result['status'] == 'STOPPED_PARALLEL_RECOVERY_REQUIRED'
    assert (root / A).read_text() == 'external process tamper'
    assert any(e == 'PARALLEL_ROLLBACK_BLOCKED' for e, _ in audit())


def test_interrupted_after_wave_zero_validation_resumes_without_reapplying_wave_zero(tmp_path, monkeypatch):
    state = setup_two_wave(tmp_path, monkeypatch)
    db = str(tmp_path / 'workflow.db')
    config = {'configurable': {'thread_id': 'interrupt-between-waves'}}
    model = Provider()

    with SqliteSaver.from_conn_string(db) as saver:
        # Interrupt before worker runs
        app = build_workflow(model, validator=validator).compile(checkpointer=saver, interrupt_before=['worker'])
        result = approve_n(app, state, config, n=3)
        assert 'worker' in app.get_state(config).next

    # Resume to completion
    with SqliteSaver.from_conn_string(db) as saver:
        app = build_workflow(model, validator=validator).compile(checkpointer=saver)
        result = app.invoke(None, config)

    assert result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert len(model.calls) == 3
    # Exactly 2 transactions applied (1 per wave, never duplicated)
    assert sum(e == 'TRANSACTION_APPLIED' for e, _ in audit()) == 2
