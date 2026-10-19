"""Sequential, opt-in continuation of a prospectively approved engineering plan.

Supports single or multiple dependent implementation tasks, generic vetted feature adapters,
no historical patch replay. Failures compensate and stop; recovery requiring another plan returns to review.
"""
import base64
import difflib
import json
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from agents.implementation_agent import implement_task
from agents.test_generation_agent import generate_spec, render, CASES, validate_spec
from governance.audit_logger import log_event
from governance.plan_lineage import approved, before_execution, link_tests, verify_test_link, source_journal, events, receipt
from governance.semantic_plan import digest
from governance.test_safety import inventory, preserve, load_baseline, assert_integrity, new_test_target, sha
from governance.file_transaction import finish_transaction, save_journal, sync_directory
from governance.test_stage import maven, require_results
from governance.downstream_sdlc import (
    TEST_PATH, scope as downstream_scope, compensate as downstream_compensate,
    test_review as downstream_test_review, tests as downstream_tests,
    validation as downstream_validation, maven_record, verify_integrated as verify
)

scope = downstream_scope
compensate = downstream_compensate


def log(state, event, details):
    log_event(state['context']['workflow_id'], 'integrated_sdlc', event, details)


def entry(state):
    try:
        c = state['context']; auth = approved(c)
        tasks = [t for t in c['tasks'] if t['execution_type'] == 'implementation']
        if not (1 <= len(tasks) <= 6):
            raise PermissionError('Sequential milestone requires 1 to 6 implementation tasks')
        from governance.task_orchestrator import topological_execution_waves
        by_id = {t['task_id']: t for t in tasks}
        waves = topological_execution_waves(tasks)
        task_order_ids = [t_id for wave in waves for t_id in wave]
        first_id = task_order_ids[0]
        ready_tasks = [{**t, 'status': 'READY'} if t['task_id'] == first_id else t for t in c['tasks']]
        task = next(t for t in ready_tasks if t['task_id'] == first_id)
        c = {**c, 'task_order_ids': task_order_ids, 'task_index': 0, 'tasks': ready_tasks, 'active_task_ids': [task['task_id']]}
        before_execution(c, task)

        from governance.feature_registry import get_feature_adapter
        adapter = get_feature_adapter(c.get('obligation_contract') or c)
        if auth['body']['test_additions'] != [adapter.test_path]:
            raise PermissionError('Exact vetted additive test authorization required')
        adapter.validate_spec(adapter.expected_spec_payload(), c.get('obligation_contract'))

        evidence = Path(state['evidence']).resolve(strict=True)
        if evidence.is_relative_to(Path(c['workspace']).resolve()):
            raise PermissionError('Evidence must be outside editable workspace')
        baseline = preserve(c['workspace'], evidence / 'originals')
        state = {**state, 'context': c, 'baseline_hash': baseline}
        baseline_result = maven(c['workspace'], evidence, 'baseline', state['cache'])
        if baseline_result['exit_code'] or not baseline_result['cases'] or any(v != 'passed' for v in baseline_result['cases'].values()):
            raise PermissionError('Original tests must genuinely execute and pass')
        assert_integrity(c['workspace'], load_baseline(evidence / 'originals', baseline))
        log(state, 'INTEGRATED_ENTRY_PASSED', {
            'authorization_hash': auth['authorization_hash'], 'baseline_hash': baseline,
            'requirements_hash': c['requirements_hash'], 'design_hash': c['approved_design_hash'],
            'validation': baseline_result
        })
        return {**state, 'baseline_validation': baseline_result, 'status': 'INTEGRATED_ENTRY_PASSED'}
    except Exception as error:
        return compensate(state, error)


def implementation(state, model):
    current = state
    try:
        c = state['context']
        task_order_ids = c.get('task_order_ids')
        task_index = c.get('task_index', 0)
        if task_order_ids and task_index < len(task_order_ids):
            current_task_id = task_order_ids[task_index]
            current_task = next(t for t in c['tasks'] if t['task_id'] == current_task_id)
        else:
            current_task = c['tasks'][0]
        prev_applied = dict(state.get('applied_changes', {}))
        prev_txs = list(state.get('transactions', []))
        tx = implement_task(current_task, Path(c['workspace']), c, provider=model)
        transactions = prev_txs + [tx]
        applied = dict(prev_applied)
        journal = source_journal(c['workspace'], tx['transaction_id'])
        for f in journal['files']:
            applied[f['path']] = base64.b64decode(f['expected_content'], validate=True)
        current = {
            **state,
            'prev_applied_changes': prev_applied,
            'prev_transactions': prev_txs,
            'transaction': tx,
            'transactions': transactions,
            'applied_changes': applied,
            'context': {
                **c,
                'implementation_transaction': tx,
                'applied_changes': applied,
            }
        }
        log(state, 'INTEGRATED_IMPLEMENTATION_APPLIED', {
            'transaction_id': tx['transaction_id'],
            'execution_hash': tx['execution_receipt']['execution_hash'],
            'proposal_hash': digest(tx['proposal'])
        })
        return {**current, 'status': 'IMPLEMENTATION_APPLIED'}
    except Exception as error:
        return compensate(current, error)


def assert_applied(state):
    c = state['context']; auth = approved(c); tx = state['transaction']; execution = tx['execution_receipt']
    receipt(c, 'ENGINEERING_EXECUTION_BOUND', execution)
    if execution['execution_hash'] != digest(execution['body']) or execution['body']['authorization_hash'] != auth['authorization_hash']:
        raise PermissionError('Execution binding changed')
    journal = source_journal(c['workspace'], tx['transaction_id'])
    if journal['status'] != 'applied' or journal['workflow_id'] != c['workflow_id']:
        raise PermissionError('Existing applied implementation transaction required')
    expected = execution['body']['expected_project_hashes']
    if {p: sha(v) for p, v in inventory(c['workspace']).items()} != expected:
        raise PermissionError('Source/test/build inventory changed after implementation')
    return journal


def source_validation(state):
    """Compile/run retained tests before asking to generate additive tests.

    Corrections cannot change the plan/task/scope. A new receipt is required for
    any such change; before_execution enforces it on every retry.
    """
    attempt = state.get('source_attempt', 0)
    try:
        assert_applied(state)
        attempt = state.get('source_attempt', 0) + 1
        phase = f'implementation-{attempt}'
        record = maven(state['context']['workspace'], state['evidence'], phase, state['cache'])
        assert_applied(state)
        passed = (record['exit_code'] == 0 and record['cases'] == state['baseline_validation']['cases'])
        if passed and state.get('required_clock_delegation'):
            from governance.design_correction import assert_delegation
            assert_delegation((Path(state['context']['workspace']) / 'url-shortener/src/main/java/url_shortener/UrlService.java').read_text())
        evidence = {
            'attempt': attempt,
            'phase': phase,
            'validation': record,
            'transaction_id': state['transaction']['transaction_id'],
            'authorization_hash': state['context']['plan_authorization']['authorization_hash']
        }
        history = state.get('source_validation_history', []) + [evidence]
        if passed:
            log(state, 'IMPLEMENTATION_SOURCE_GATE_PASSED', evidence)
            c = state['context']
            task_order_ids = c.get('task_order_ids')
            task_index = c.get('task_index', 0)
            if task_order_ids and task_index + 1 < len(task_order_ids):
                finish_transaction(state['transaction']['transaction_id'], Path(c['workspace']), state['transaction']['paths'], passed=True)
                next_index = task_index + 1
                prev_id = task_order_ids[task_index]
                next_task_id = task_order_ids[next_index]
                next_tasks = [
                    {**t, 'status': 'COMPLETED'} if t['task_id'] == prev_id
                    else {**t, 'status': 'READY'} if t['task_id'] == next_task_id
                    else t
                    for t in c['tasks']
                ]
                next_task = next(t for t in next_tasks if t['task_id'] == next_task_id)
                next_context = {
                    **c,
                    'task_index': next_index,
                    'tasks': next_tasks,
                    'active_task_ids': [next_task_id],
                    'applied_changes': state['applied_changes'],
                }
                before_execution(next_context, next_task)
                log(state, 'NEXT_TASK_READY', {'task_id': next_task_id, 'task_index': next_index})
                return {
                    **state,
                    'context': next_context,
                    'source_attempt': attempt,
                    'source_validation_history': history,
                    'status': 'NEXT_TASK_READY'
                }
            log(state, 'AWAITING_TEST_GENERATION_APPROVAL', {
                'transaction_id': state['transaction']['transaction_id'],
                'authorization_hash': evidence['authorization_hash']
            })
            return {
                **state,
                'source_attempt': attempt,
                'source_validation_history': history,
                'status': 'AWAITING_TEST_GENERATION_APPROVAL'
            }
        log(state, 'IMPLEMENTATION_SOURCE_GATE_FAILED', evidence)
        stopped = compensate(state, 'Implementation compilation/original-test gate failed')
        if stopped['recovery'] != 'rolled_back' or attempt >= 3:
            return {**stopped, 'source_attempt': attempt, 'source_validation_history': history}
        c = state['context']; output = (Path(state['evidence']) / phase / 'maven.log').read_text()
        task_order_ids = c.get('task_order_ids')
        task_index = c.get('task_index', 0)
        current_task_id = task_order_ids[task_index] if task_order_ids else c['tasks'][0]['task_id']
        current_task = next(t for t in c['tasks'] if t['task_id'] == current_task_id)
        saved_applied = state.get('prev_applied_changes', {})
        saved_txs = state.get('prev_transactions', [])
        c = {
            **c,
            'previous_implementation_proposal': state['transaction']['proposal'],
            'previous_validation_result': {'passed': False, 'exit_code': record['exit_code'], 'output': output},
            'failure_context': {
                'category': 'java_compilation_or_original_tests',
                'validation': record,
                'message': 'Correct only the existing approved proposal. No plan, task or file-scope change is authorized.'
            },
            'implementation_transaction': {},
            'applied_changes': saved_applied,
        }
        before_execution(c, current_task)
        log(state, 'INTEGRATED_SAME_PLAN_RETRY', {
            'attempt': attempt + 1,
            'plan_hash': digest(c['plan']),
            'authorization_hash': c['plan_authorization']['authorization_hash'],
            'diagnostic_hash': sha(output.encode())
        })
        return {
            **state,
            'context': c,
            'transaction': {},
            'transactions': saved_txs,
            'applied_changes': saved_applied,
            'source_attempt': attempt,
            'source_validation_history': history,
            'status': 'SOURCE_RETRY'
        }
    except Exception as error:
        return compensate({**state, 'source_attempt': attempt}, error)


def test_review(state):
    return downstream_test_review(state)


def tests(state, model):
    return downstream_tests(state, model, link_fn=os.link)


def validation(state):
    return downstream_validation(state, validator=maven)


def build_workflow(model):
    from governance.documentation_release import review_tests, documentation, evaluate, final_review
    graph = StateGraph(dict)
    from governance.design_correction import begin_continuation
    from governance.test_replay import verify_replay
    graph.add_node('correction_entry', begin_continuation)
    graph.add_node('test_replay_entry', verify_replay)
    graph.add_node('entry', entry); graph.add_node('implementation', lambda s: implementation(s, model))
    graph.add_node('source_validation', source_validation)
    graph.add_node('test_generation_review', test_review); graph.add_node('test_generation', lambda s: tests(s, model))
    graph.add_node('maven_validation', validation); graph.add_node('test_results_review', review_tests)
    graph.add_node('documentation', lambda s: documentation(s, model)); graph.add_node('readiness', evaluate)
    graph.add_node('final_review', final_review)
    graph.add_conditional_edges(START, lambda s: 'replay' if s.get('test_replay_receipt') else 'correct' if s.get('design_correction_receipt') else 'new',
        {'replay': 'test_replay_entry', 'correct': 'correction_entry', 'new': 'entry'})
    graph.add_edge('test_replay_entry', 'test_generation_review')
    graph.add_edge('correction_entry', 'implementation')
    for node, next_node in [('entry', 'implementation'), ('implementation', 'source_validation'),
        ('test_generation_review', 'test_generation'), ('test_generation', 'maven_validation'), ('maven_validation', 'test_results_review')]:
        graph.add_conditional_edges(node, lambda s: 'stop' if s['status'].startswith('STOPPED') else 'next', {'stop': END, 'next': next_node})
    graph.add_conditional_edges('source_validation', lambda s: 'stop' if s['status'].startswith('STOPPED') else 'retry' if s['status'] == 'SOURCE_RETRY' else 'next_task' if s['status'] == 'NEXT_TASK_READY' else 'review',
        {'stop': END, 'retry': 'implementation', 'next_task': 'implementation', 'review': 'test_generation_review'})
    graph.add_conditional_edges('test_results_review', lambda s: 'next' if s['status'] == 'TEST_RESULTS_APPROVED' else 'stop', {'next': 'documentation', 'stop': END})
    graph.add_conditional_edges('documentation', lambda s: 'next' if s['status'] == 'DOCUMENTATION_VALIDATED' else 'retry' if s['status'] == 'DOCUMENTATION_RETRY' else 'stop',
        {'next': 'readiness', 'retry': 'documentation', 'stop': END})
    graph.add_edge('readiness', 'final_review'); graph.add_edge('final_review', END)
    return graph
