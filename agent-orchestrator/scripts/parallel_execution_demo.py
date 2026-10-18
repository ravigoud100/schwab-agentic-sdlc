"""Deterministic orchestration acceptance with genuine Maven, not a live AI demo.

Synthetic human decisions apply ONLY to fixture tasks in disposable copied
projects. No engineering plan, release or deployment approval is granted.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.parallel_execution import build_workflow
from governance.task_orchestrator import task_definition_hash
from governance.test_safety import inventory, sha
from llm.provider import ModelResponse

REPO = Path(__file__).resolve().parents[2]
SERVICE = 'url-shortener/src/main/java/url_shortener/UrlService.java'
CONTROLLER = 'url-shortener/src/main/java/url_shortener/UrlController.java'


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=lambda v: {'sha256': sha(v)} if isinstance(v, bytes) else str(v))+'\n')


def run(directory, cache, negative):
    directory.mkdir(); root = directory/'workspace'; root.mkdir()
    shutil.copytree(REPO/'url-shortener', root/'url-shortener', ignore=shutil.ignore_patterns('target', '.git', '.jdk-25'))
    original = inventory(root)
    tasks = [{'task_id': name, 'description': 'Controlled orchestration fixture: append a Java comment only',
              'dependencies': [], 'status': 'PENDING', 'execution_type': 'implementation', 'allowed_files': [path]}
             for name, path in [('service-fixture', SERVICE), ('controller-fixture', CONTROLLER)]]
    if negative:tasks[1]['description'] = 'Controlled negative fixture: append an invalid Java token to force compilation failure'
    context = {'workflow_id': 'parallel-fixture-'+directory.name, 'workspace': str(root),
               'requirement': 'Synthetic executor verification only; no product requirement change',
               'tasks': tasks, 'approval': 'approve', 'approved_task_hash': task_definition_hash(tasks),
               'plan': json.dumps({'tasks': tasks}), 'fixture_only': True}
    barrier = threading.Barrier(2)
    class FixtureProvider:
        def invoke(self, prompt, **kwargs):
            task = json.loads(prompt.split('APPROVED TASK (filtered production scope):\n')[1].split('\nORIGINAL COMPLETE SOURCES')[0])
            path = task['allowed_files'][0]
            barrier.wait(timeout=10)
            suffix = '\nINTENTIONALLY_INVALID_PARALLEL_FIXTURE\n' if negative and path == CONTROLLER else '\n// Deterministic parallel orchestration fixture.\n'
            return ModelResponse(json.dumps({'files': [{'path': path, 'content': original[path].decode()+suffix}]}))
    audit_logger.DB_PATH = directory/'audit.db'
    config = {'configurable': {'thread_id': context['workflow_id']}}
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app = build_workflow(FixtureProvider()).compile(checkpointer=saver)
        result = app.invoke({'context': context, 'evidence': str(directory/'evidence'), 'cache': str(cache)}, config)
        for _ in tasks:
            assert app.get_state(config).next == ('review',)
            # Synthetic fixture decision, explicitly not a human approval receipt
            # for a live product plan; isolated test databases are retained.
            result = app.invoke(Command(resume='approve'), config)
        assert not app.get_state(config).next
        checkpoint_count = len(list(saver.list(config)))
    expected = 'STOPPED_PARALLEL_ROLLED_BACK' if negative else 'AWAITING_PARALLEL_RESULTS_REVIEW'
    assert result['status'] == expected, result
    current = inventory(root)
    changed = sorted(p for p in current if current[p] != original[p])
    assert set(current) == set(original)
    assert changed == ([] if negative else sorted([SERVICE, CONTROLLER]))
    assert result['baseline_validation']['exit_code'] == 0
    assert result['validation']['exit_code'] != 0 if negative else result['validation']['exit_code'] == 0
    with sqlite3.connect(directory/'audit.db') as conn:
        audit = [{'id': i, 'event': e, 'details': json.loads(d)} for i, e, d in conn.execute('select id,event,details from audit_events order by id')]
    events = [e['event'] for e in audit]
    assert events.count('PARALLEL_TASK_PREPARED') == 2
    assert events.index('PARALLEL_SYNCHRONIZED') < events.index('TRANSACTION_APPLIED') < events.index('PARALLEL_VALIDATION_STARTED')
    dump(directory/'result.json', result); dump(directory/'audit.json', audit)
    journals = [json.loads(p.read_text()) for p in (root/'.agent-transactions').glob('*.json')]
    dump(directory/'journals.json', journals)
    summary = {'provenance': 'Controlled models and synthetic approval fixtures; genuine Maven subprocesses',
               'live_engineering_plan_approved': False, 'status': result['status'],
               'workflow_id': context['workflow_id'], 'checkpoint_count': checkpoint_count,
               'baseline': result['baseline_validation'], 'combined': result['validation'],
               'changed_files': changed, 'journal_statuses': [j['status'] for j in journals],
               'original_tests_and_build_preserved': True, 'negative_fixture': negative,
               'concurrency_proof': 'Both provider calls rendezvoused at threading.Barrier(2)',
               'synchronization_order_verified': True}
    dump(directory/'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--maven-cache', type=Path, required=True)
    args = parser.parse_args(); directory = args.directory.resolve(); directory.mkdir(exist_ok=False)
    summaries = {name: run(directory/name, args.maven_cache, negative) for name, negative in [('success', False), ('compilation-failure', True)]}
    dump(directory/'summary.json', summaries)
    print(json.dumps(summaries, indent=2))
