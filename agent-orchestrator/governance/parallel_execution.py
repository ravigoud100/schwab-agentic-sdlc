"""Opt-in, approval-bound parallel proposal execution and transactional fan-in.

Workers never write source. LangGraph's superstep barrier precedes one whole-set
transaction and one Maven gate. This bounded version supports 2..4 independent
production-only tasks, not concurrent filesystem writers or dependent DAG waves.
"""
import base64
import json
import operator
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt
from agents.implementation_agent import implement_task
from governance import audit_logger
from governance.audit_logger import log_event
from governance.file_transaction import apply_transaction, finish_transaction, ensure_workspace_ready
from governance.requirements import assert_plan_current
from governance.semantic_plan import digest
from governance.source_policy import authorized_java_target
from governance.task_orchestrator import task_definition_hash, validate_task_plan
from governance.test_safety import preserve, load_baseline, assert_integrity, sha
from governance.test_stage import maven

MAX_ATTEMPTS = 3  # Initial generation plus the existing two-retry budget.


class ParallelState(TypedDict, total=False):
    context: dict
    evidence: str
    cache: str
    baseline_hash: str
    binding: dict
    receipts: dict
    outcomes: Annotated[list, operator.add]
    status: str
    error: str
    baseline_validation: dict
    validation: dict
    transaction: dict
    task_checkpoints: dict


def event(state, name, details):
    log_event(state['context']['workflow_id'], 'parallel_executor', name, details)


def binding(context, baseline_hash):
    return {'workflow_id': context['workflow_id'],
            'workspace': str(Path(context['workspace']).resolve(strict=True)),
            'requirements_hash': context.get('requirements_hash', ''),
            'requirement_version': context.get('requirement_version'),
            'design_hash': digest(context.get('design_artifact', {})),
            'contract_hash': digest(context.get('obligation_contract', {})),
            'plan_hash': digest(context['plan']),
            'tasks_hash': task_definition_hash(context['tasks']),
            'baseline_hash': baseline_hash}


def check_context(context):
    assert_plan_current(context, approved=True)
    tasks = context['tasks']; validate_task_plan(tasks)
    if context.get('approval') != 'approve' or context.get('approved_task_hash') != task_definition_hash(tasks):
        raise PermissionError('Unchanged human-approved plan required')
    # The older sequential receipt deliberately cannot authorize this execution.
    if context.get('plan_lineage_required') or context.get('integrated_sdlc'):
        raise PermissionError('Sequential authorization cannot be adopted by parallel execution')
    if not 2 <= len(tasks) <= 4:
        raise ValueError('Parallel execution supports two to four independent tasks')
    root = Path(context['workspace']); seen = set()
    for task in tasks:
        if (task['dependencies'] or task['status'] not in {'PENDING', 'READY'}
                or task.get('execution_type', 'implementation') != 'implementation'):
            raise PermissionError('Only unstarted independent implementation tasks are supported')
        if not task['allowed_files'] or len(set(task['allowed_files'])) != len(task['allowed_files']):
            raise PermissionError('Unique nonempty production scope required')
        for path in task['allowed_files']:
            if not path.startswith('url-shortener/src/main/java/'):
                raise PermissionError('Existing tests and build files are read-only')
            target = authorized_java_target(path, root, task['allowed_files'])
            # Reject hard-link aliases as well as lexical and symlink aliases.
            stat = target.stat(); key = (stat.st_dev, stat.st_ino)
            if key in seen or stat.st_nlink != 1:
                raise PermissionError('Parallel file scopes overlap or alias')
            seen.add(key)


def entry(state):
    try:
        check_context(state['context'])
        root = Path(state['context']['workspace']); ensure_workspace_ready(root)
        evidence = Path(state['evidence']).resolve()
        if evidence.is_relative_to(root.resolve()):
            raise PermissionError('Independent evidence directory must be outside workspace')
        evidence.mkdir(parents=True, exist_ok=False)
        baseline_hash = preserve(root, evidence/'originals')
        result = {**state, 'baseline_hash': baseline_hash,
                  'binding': binding(state['context'], baseline_hash), 'outcomes': [], 'receipts': {},
                  'status': 'PARALLEL_REVIEW_REQUIRED'}
        event(result, 'PARALLEL_ENTRY_PASSED', result['binding'])
        return result
    except Exception as error:
        event(state, 'PARALLEL_ENTRY_REJECTED', {'error': str(error)})
        return {**state, 'status': 'STOPPED_PARALLEL_ENTRY', 'error': str(error)}


def recheck(state, *, applied=None):
    check_context(state['context'])
    if binding(state['context'], state['baseline_hash']) != state['binding']:
        raise PermissionError('Parallel requirement/design/plan/baseline binding changed')
    baseline = load_baseline(Path(state['evidence'])/'originals', state['baseline_hash'])
    assert_integrity(Path(state['context']['workspace']), baseline, applied)
    return baseline


def task_receipt(state, task):
    return {'binding_hash': digest(state['binding']), 'task_id': task['task_id'],
            'task_hash': task_definition_hash([task])}


def receipt_exists(state, receipt):
    import sqlite3
    with sqlite3.connect(f'file:{Path(audit_logger.DB_PATH).resolve()}?mode=ro', uri=True) as conn:
        rows = conn.execute('SELECT details FROM audit_events WHERE workflow_id=? AND event=?',
                            (state['context']['workflow_id'], 'PARALLEL_TASK_APPROVED')).fetchall()
    return any(json.loads(row[0]) == receipt for row in rows)


def review(state):
    try:
        recheck(state)
    except Exception as error:
        event(state, 'PARALLEL_APPROVAL_INVALID', {'error': str(error)})
        return {'status': 'STOPPED_PARALLEL_APPROVAL_INVALID', 'error': str(error)}
    receipts = {}
    for task in state['context']['tasks']:
        receipt = task_receipt(state, task)
        decision = interrupt({'type': 'PARALLEL_TASK_APPROVAL', 'task': task,
                              'binding': state['binding'], 'receipt': receipt,
                              'message': 'Approve this task only. Disjoint proposals join before source application and Maven. No release authorization.'})
        if decision != 'approve':
            event(state, 'PARALLEL_TASK_REJECTED', receipt)
            return {'status': 'STOPPED_PARALLEL_REJECTED'}
        if not receipt_exists(state, receipt):
            event(state, 'PARALLEL_TASK_APPROVED', receipt)
        receipts[task['task_id']] = receipt
    return {'receipts': receipts, 'status': 'PARALLEL_APPROVED'}


def assert_receipts(state):
    tasks = state['context']['tasks']
    if set(state['receipts']) != {t['task_id'] for t in tasks}:
        raise PermissionError('Every concurrent task needs independent approval')
    for task in tasks:
        expected = task_receipt(state, task)
        if state['receipts'][task['task_id']] != expected or not receipt_exists(state, expected):
            raise PermissionError('Missing or stale task approval receipt')


def successful(record, expected=None):
    return (record.get('exit_code') == 0 and bool(record.get('cases'))
            and all(v == 'passed' for v in record['cases'].values())
            and (expected is None or set(record['cases']) == set(expected)))


def build_workflow(provider, *, validator=maven):
    def baseline(state):
        try:
            recheck(state); assert_receipts(state)
            record = validator(Path(state['context']['workspace']), Path(state['evidence']), 'baseline', state['cache'])
            recheck(state)
            if not successful(record): raise RuntimeError('Original Maven tests must all execute and pass')
            event(state, 'PARALLEL_BASELINE_PASSED', record)
            return {'baseline_validation': record, 'status': 'PARALLEL_READY'}
        except Exception as error:
            event(state, 'PARALLEL_BASELINE_REJECTED', {'error': str(error)})
            return {'status': 'STOPPED_PARALLEL_BASELINE', 'error': str(error)}

    def dispatch(state):
        return [Send('worker', {**state, 'task_id': task['task_id']}) for task in state['context']['tasks']]

    def worker(state):
        task = next(t for t in state['context']['tasks'] if t['task_id'] == state['task_id'])
        directory = Path(state['evidence']) / ('task-' + digest(task['task_id']))
        try:
            directory.mkdir(exist_ok=False)
        except OSError as error:
            # An interrupted, uncheckpointed worker must not silently reset its
            # generation budget or overwrite its retained attempt evidence.
            event(state, 'PARALLEL_WORKER_RECOVERY_REQUIRED', {'task_id': task['task_id'], 'error': str(error)})
            return {'outcomes': [{'task_id': task['task_id'], 'ok': False,
                                 'attempts': 0, 'error': 'Existing worker evidence requires review'}]}
        attempts = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                recheck(state); assert_receipts(state)
                context = {**state['context'], 'tasks': [{**t, 'status': 'READY'} for t in state['context']['tasks']],
                           'active_task_ids': [task['task_id']], 'implementation_response_format': 'complete_files'}
                selected = next(t for t in context['tasks'] if t['task_id'] == task['task_id'])
                if attempts:
                    context['failure_context'] = {'category': 'model_output', 'previous_attempts': attempts}
                class Recorded:
                    def invoke(self, prompt, **kwargs):
                        (directory/f'{attempt}-prompt.txt').write_text(prompt)
                        response = provider.invoke(prompt, **kwargs)
                        (directory/f'{attempt}-response.txt').write_text(response.text)
                        return response
                event(state, 'PARALLEL_ATTEMPT_STARTED', {'task_id': task['task_id'], 'attempt': attempt})
                result = implement_task(selected, Path(context['workspace']), context, provider=Recorded(), prepare_only=True)
                recheck(state)
                encoded = {k: {p: base64.b64encode(v).decode() for p, v in result[k].items()} for k in ('proposals', 'originals')}
                outcome = {'task_id': task['task_id'], 'ok': True, 'attempts': attempt,
                           'receipt': state['receipts'][task['task_id']], **encoded}
                (directory/'outcome.json').write_text(json.dumps(outcome, indent=2))
                event(state, 'PARALLEL_TASK_PREPARED', {'task_id': task['task_id'], 'attempt': attempt,
                      'proposed_hashes': {p: sha(v) for p, v in result['proposals'].items()}})
                return {'outcomes': [outcome]}
            except Exception as error:
                detail = {'task_id': task['task_id'], 'attempt': attempt,
                          'error_type': type(error).__name__, 'error': str(error),
                          'evidence': getattr(error, 'attempt_details', {})}
                attempts.append(detail)
                (directory/f'{attempt}-error.json').write_text(json.dumps(detail, default=str, indent=2))
                event(state, 'PARALLEL_ATTEMPT_FAILED', detail)
                # Authorization/infrastructure/conflict errors never regenerate.
                if not isinstance(error, (ValueError, KeyError, TypeError)):
                    break
        outcome = {'task_id': task['task_id'], 'ok': False, 'attempts': len(attempts), 'errors': attempts}
        (directory/'outcome.json').write_text(json.dumps(outcome, default=str, indent=2))
        return {'outcomes': [outcome]}

    def join(state):
        transaction = None
        try:
            baseline_files = recheck(state); assert_receipts(state)
            outcomes = state['outcomes']; expected = {t['task_id']: t for t in state['context']['tasks']}
            if len(outcomes) != len(expected) or {o['task_id'] for o in outcomes} != set(expected):
                raise PermissionError('Incomplete or duplicate parallel outcomes')
            event(state, 'PARALLEL_SYNCHRONIZED', {'task_ids': sorted(expected), 'outcome_hash': digest(outcomes)})
            if not all(o['ok'] for o in outcomes):
                return {'status': 'STOPPED_PARALLEL_WORKER_FAILED'}
            proposals = {}; originals = {}; checkpoints = {}
            for outcome in outcomes:
                task = expected[outcome['task_id']]
                if outcome['receipt'] != task_receipt(state, task): raise PermissionError('Outcome approval changed')
                if set(outcome['proposals']) != set(task['allowed_files']) or set(outcome['originals']) != set(task['allowed_files']):
                    raise PermissionError('Worker proposal scope changed')
                for path, data in outcome['proposals'].items():
                    if path in proposals: raise PermissionError('Overlapping worker proposals')
                    original = base64.b64decode(outcome['originals'][path], validate=True)
                    proposed = base64.b64decode(data, validate=True)
                    if original != baseline_files[path] or proposed == original:
                        raise PermissionError('Worker source baseline changed or unchanged proposal')
                    proposals[path] = proposed; originals[path] = original
            transaction = apply_transaction(proposals, originals, Path(state['context']['workspace']), list(proposals), state['context']['workflow_id'])
            for task_id, task in expected.items():
                checkpoints[task_id] = {'receipt': state['receipts'][task_id], 'transaction_id': transaction['transaction_id'],
                    'checkpoints': [transaction['transaction_id'] + ':' + p for p in task['allowed_files']]}
            event(state, 'PARALLEL_TASK_CHECKPOINTS_BOUND', checkpoints)
            return {'transaction': transaction, 'task_checkpoints': checkpoints, 'status': 'PARALLEL_APPLIED'}
        except Exception as error:
            return stop(state, transaction, error)

    def stop(state, transaction, error):
        status = 'STOPPED_PARALLEL_FAILED'
        if transaction:
            try:
                finish_transaction(transaction['transaction_id'], Path(state['context']['workspace']), transaction['paths'], passed=False)
                event(state, 'PARALLEL_ROLLED_BACK', {'transaction_id': transaction['transaction_id']})
                status = 'STOPPED_PARALLEL_ROLLED_BACK'
            except Exception as recovery_error:
                status = 'STOPPED_PARALLEL_RECOVERY_REQUIRED'
                event(state, 'PARALLEL_ROLLBACK_BLOCKED', {'error': str(recovery_error), 'transaction_id': transaction['transaction_id']})
        event(state, 'PARALLEL_FAILED', {'error': str(error), 'status': status})
        return {'status': status, 'error': str(error)}

    def validation(state):
        tx = state['transaction']; root = Path(state['context']['workspace'])
        try:
            from governance.plan_lineage import source_journal
            journal = source_journal(root, tx['transaction_id'])
            changes = {p: base64.b64decode(data, validate=True)
                       for outcome in state['outcomes'] for p, data in outcome['proposals'].items()}
            baseline_files = load_baseline(Path(state['evidence'])/'originals', state['baseline_hash'])
            if (journal['status'] != 'applied' or journal['workflow_id'] != state['context']['workflow_id']
                    or len(journal['files']) != len(changes)
                    or {e['path'] for e in journal['files']} != set(changes)):
                raise PermissionError('Joined transaction journal identity changed')
            for entry in journal['files']:
                path = entry['path']
                if (base64.b64decode(entry['expected_content'], validate=True) != changes[path]
                        or entry['expected_current_hash'] != sha(changes[path])
                        or tx['expected_hashes'][path] != sha(changes[path])
                        or base64.b64decode(entry['original_content'], validate=True) != baseline_files[path]
                        or entry['original_hash'] != sha(baseline_files[path])):
                    raise PermissionError('Joined transaction checkpoint/hash changed')
            recheck(state, applied=changes); assert_receipts(state)
            event(state, 'PARALLEL_VALIDATION_STARTED', {'transaction_id': tx['transaction_id'], 'tasks': sorted(state['task_checkpoints'])})
            record = validator(root, Path(state['evidence']), 'combined', state['cache'])
            recheck(state, applied=changes); assert_receipts(state)
            if not successful(record, state['baseline_validation']['cases']):
                result = stop(state, tx, RuntimeError('Combined Maven validation failed or original tests missing/skipped'))
                return {**result, 'validation': record}
            finish_transaction(tx['transaction_id'], root, tx['paths'], passed=True)
            event(state, 'PARALLEL_VALIDATION_PASSED', {'transaction_id': tx['transaction_id'], 'validation': record})
            return {'validation': record, 'status': 'AWAITING_PARALLEL_RESULTS_REVIEW'}
        except Exception as error:
            return stop(state, tx, error)

    graph = StateGraph(ParallelState)
    for name, node in [('entry', entry), ('review', review), ('baseline', baseline), ('worker', worker), ('join', join), ('validation', validation)]:
        graph.add_node(name, node)
    graph.add_edge(START, 'entry')
    graph.add_conditional_edges('entry', lambda s: END if s['status'].startswith('STOPPED') else 'review')
    graph.add_conditional_edges('review', lambda s: END if s['status'].startswith('STOPPED') else 'baseline')
    graph.add_conditional_edges('baseline', lambda s: END if s['status'].startswith('STOPPED') else dispatch(s))
    graph.add_edge('worker', 'join')  # LangGraph fan-in waits for the entire worker superstep.
    graph.add_conditional_edges('join', lambda s: 'validation' if s['status'] == 'PARALLEL_APPLIED' else END)
    graph.add_edge('validation', END)
    return graph
