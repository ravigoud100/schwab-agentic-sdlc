"""End-to-end parallel SDLC acceptance suite.

Validates concurrent multi-task execution, multi-transaction validation,
shared downstream test generation with genuine Maven execution,
explicit approval interrupts, documentation, release readiness, and whole-set rollback.
"""
import base64
import json
from pathlib import Path
import shutil
import sqlite3
import threading
from unittest.mock import Mock

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agents.test_generation_agent import CASES
from governance import audit_logger
from governance.parallel_execution import build_workflow
from governance.semantic_plan import custom_expiration_contract, digest
from governance.task_orchestrator import task_definition_hash
from governance.test_safety import sha
from governance.test_stage import maven
from llm.provider import ModelResponse

TASK1_FILE = 'url-shortener/src/main/java/url_shortener/RedirectController.java'
TASK2_FILE = 'url-shortener/src/main/java/url_shortener/ShortUrl.java'
TEST_PATH = 'url-shortener/src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java'


class ConcurrentDeterministicModel:
    """Deterministic model simulating concurrent workers via threading barrier."""

    def __init__(self, barrier=None):
        self.barrier = barrier
        self.invoked_files = []
        self.lock = threading.Lock()

    def invoke(self, prompt, **kwargs):
        stage = kwargs.get('stage')
        if stage == 'test_generation':
            expected = {'cases': [{'obligation_id': k, 'spec': v} for k, v in CASES.items()]}
            return ModelResponse(json.dumps(expected))

        if stage == 'documentation':
            # Extract catalog from prompt
            catalog_marker = 'Authoritative evidence-derived fact catalog:\n'
            raw_catalog = prompt.split(catalog_marker)[1].split('\nPrevious validation errors:')[0]
            catalog = json.loads(raw_catalog)
            selection = {sec: list(items.keys()) for sec, items in catalog.items()}
            return ModelResponse(json.dumps(selection))

        # Implementation worker stage
        task_marker = 'APPROVED TASK (filtered production scope):\n'
        task_data = json.loads(prompt.split(task_marker)[1].split('\nORIGINAL COMPLETE SOURCES')[0])
        path = task_data['allowed_files'][0]

        with self.lock:
            self.invoked_files.append(path)

        if self.barrier:
            self.barrier.wait(timeout=10)

        # Return minimal safe valid Java modifications
        raw_sources = prompt.split('ORIGINAL COMPLETE SOURCES, restored before this attempt:\n')[1].split('\nDECLARED IMPORTS PER FILE:')[0]
        sources = json.loads(raw_sources)
        orig_content = sources[path]
        modified = orig_content + f'\n// Verified parallel task edit on {path}\n'
        return ModelResponse(json.dumps({'files': [{'path': path, 'content': modified}]}))


def setup_parallel_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree('url-shortener', workspace / 'url-shortener')

    # Ensure git repo marker
    (workspace / '.git').mkdir(exist_ok=True)

    tasks = [
        {
            'task_id': 'task-redirect',
            'description': 'Enhance redirect controller',
            'dependencies': [],
            'status': 'PENDING',
            'execution_type': 'implementation',
            'allowed_files': [TASK1_FILE],
        },
        {
            'task_id': 'task-shorturl',
            'description': 'Enhance short url entity',
            'dependencies': [],
            'status': 'PENDING',
            'execution_type': 'implementation',
            'allowed_files': [TASK2_FILE],
        },
    ]

    context = {
        'workflow_id': 'parallel-sdlc-acceptance-run',
        'workspace': str(workspace),
        'requirement': 'custom expiration with concurrent tasks',
        'approval': 'approve',
        'tasks': tasks,
        'approved_task_hash': task_definition_hash(tasks),
        'plan': json.dumps({'tasks': tasks, 'description': 'Acceptance parallel plan'}),
    }

    db_path = tmp_path / 'audit.db'
    monkeypatch.setattr(audit_logger, 'DB_PATH', db_path)

    cache_dir = str(Path.home() / '.m2/repository')
    evidence_dir = tmp_path / 'parallel-evidence'

    return {
        'context': context,
        'evidence': str(evidence_dir),
        'cache': cache_dir,
        'workspace': workspace,
    }


def test_concurrent_tasks_complete_full_sdlc_with_genuine_maven(tmp_path, monkeypatch):
    """Verifies concurrent execution, genuine Maven, and full downstream SDLC lifecycle."""
    env = setup_parallel_workspace(tmp_path, monkeypatch)
    ws = env['workspace']
    orig1 = (ws / TASK1_FILE).read_bytes()
    orig2 = (ws / TASK2_FILE).read_bytes()

    barrier = threading.Barrier(2)
    model = ConcurrentDeterministicModel(barrier=barrier)

    db_file = str(tmp_path / 'graph.db')
    with SqliteSaver.from_conn_string(db_file) as saver:
        workflow = build_workflow(model, validator=maven, downstream=True)
        app = workflow.compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'parallel-run-thread'}}

        # 1. Entry -> Task 1 Approval Interrupt
        result = app.invoke(
            {'context': env['context'], 'evidence': env['evidence'], 'cache': env['cache']},
            config,
        )
        assert len(result['__interrupt__']) == 1
        assert result['__interrupt__'][0].value['type'] == 'PARALLEL_TASK_APPROVAL'
        assert result['__interrupt__'][0].value['task']['task_id'] == 'task-redirect'

        # 2. Resume -> Task 2 Approval Interrupt
        result = app.invoke(Command(resume='approve'), config)
        assert len(result['__interrupt__']) == 1
        assert result['__interrupt__'][0].value['type'] == 'PARALLEL_TASK_APPROVAL'
        assert result['__interrupt__'][0].value['task']['task_id'] == 'task-shorturl'

        # 3. Resume -> Concurrent workers run, genuine baseline + combined Maven pass, pauses at test scope
        result = app.invoke(Command(resume='approve'), config)

        # Confirm concurrent worker invocation took place
        assert len(model.invoked_files) == 2
        assert set(model.invoked_files) == {TASK1_FILE, TASK2_FILE}

        # Confirm both transactions were applied to disk
        assert (ws / TASK1_FILE).read_bytes() != orig1
        assert (ws / TASK2_FILE).read_bytes() != orig2
        assert not (ws / TEST_PATH).exists()

        # Confirm test scope approval interrupt
        assert len(result['__interrupt__']) == 1
        assert result['__interrupt__'][0].value['type'] == 'INTEGRATED_TEST_GENERATION_APPROVAL'
        scope = result['__interrupt__'][0].value['scope']
        assert scope['test_path'] == TEST_PATH
        assert len(scope['transactions']) == 1  # 1 joined multi-file transaction in wave 0

        # 4. Resume -> Test generation, genuine Maven (36 tests pass), pauses at test results review
        result = app.invoke(Command(resume='approve'), config)
        assert len(result['__interrupt__']) == 1
        assert result['__interrupt__'][0].value['type'] == 'TEST_RESULTS_REVIEW'
        review_value = result['__interrupt__'][0].value
        assert review_value['workspace'] == str(ws.resolve())

        # Verify real Maven results: baseline 32, positive 36 (32 baseline + 4 additive)
        validation_records = review_value['validation']
        assert validation_records['baseline']['exit_code'] == 0
        assert len(validation_records['baseline']['cases']) == 32
        assert validation_records['positive']['exit_code'] == 0
        assert len(validation_records['positive']['cases']) == 36
        for case_name in (
            'futureExpiration', 'pastExpiration', 'equalNowExpiration', 'missingExpiration'
        ):
            full_case = f'url_shortener.GeneratedExpirationBoundaryTest#{case_name}'
            assert validation_records['positive']['cases'][full_case] == 'passed'

        # 5. Resume -> Documentation generated, Release Readiness evaluated, pauses at Final Review
        result = app.invoke(Command(resume='approve'), config)
        assert len(result['__interrupt__']) == 1
        assert result['__interrupt__'][0].value['type'] == 'FINAL_RELEASE_REVIEW'

        readiness = result['__interrupt__'][0].value['report']
        # Strict enforcement: deployment_authorized is ALWAYS False
        assert readiness['deployment_authorized'] is False
        assert readiness['status'] == 'BLOCKED'
        assert 'human_security_signoff' in readiness['blockers']

        # Confirm documentation was generated and verified
        doc_path = Path(env['evidence']) / 'engineering-notes.md'
        assert doc_path.exists()
        doc_content = doc_path.read_text()
        assert '# Verified engineering documentation' in doc_content

        # 6. Resume -> Final human review records decision and completes
        result = app.invoke(Command(resume='approve'), config)
        assert result['status'] == 'STOPPED_RELEASE_BLOCKED'  # Blocked readiness halts at release gate
        assert not app.get_state(config).next


def test_stale_approvals_and_invalid_transaction_hashes_are_rejected(tmp_path, monkeypatch):
    """Verifies that tampered hashes or stale scope approvals immediately fail closed."""
    env = setup_parallel_workspace(tmp_path, monkeypatch)
    model = ConcurrentDeterministicModel()

    db_file = str(tmp_path / 'graph.db')
    with SqliteSaver.from_conn_string(db_file) as saver:
        workflow = build_workflow(model, validator=maven, downstream=True)
        app = workflow.compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'tamper-test-thread'}}

        # Approve tasks
        app.invoke({'context': env['context'], 'evidence': env['evidence'], 'cache': env['cache']}, config)
        app.invoke(Command(resume='approve'), config)
        result = app.invoke(Command(resume='approve'), config)
        assert result['__interrupt__'][0].value['type'] == 'INTEGRATED_TEST_GENERATION_APPROVAL'

        # Tamper with an applied file before test generation approval
        (env['workspace'] / TASK1_FILE).write_bytes(b'tampered unauthorized content')

        # Resume should fail closed and compensate
        result = app.invoke(Command(resume='approve'), config)
        assert result['status'] == 'STOPPED_PARALLEL_STAGE'
        assert ('Unauthorized project/test/build change' in result.get('error', '')
                or 'Source file changed externally' in result.get('error', ''))
        assert result.get('recovery') == 'blocked: Source changed externally: url-shortener/src/main/java/url_shortener/RedirectController.java'


def test_downstream_failure_triggers_whole_set_rollback(tmp_path, monkeypatch):
    """Verifies that failures in downstream stages roll back all parallel transactions safely."""
    env = setup_parallel_workspace(tmp_path, monkeypatch)
    ws = env['workspace']
    orig1 = (ws / TASK1_FILE).read_bytes()
    orig2 = (ws / TASK2_FILE).read_bytes()

    # Fail during test generation
    class FailingModel(ConcurrentDeterministicModel):
        def invoke(self, prompt, **kwargs):
            if kwargs.get('stage') == 'test_generation':
                # Return invalid JSON
                return ModelResponse('{"invalid": true}')
            return super().invoke(prompt, **kwargs)

    model = FailingModel()
    db_file = str(tmp_path / 'graph.db')
    with SqliteSaver.from_conn_string(db_file) as saver:
        workflow = build_workflow(model, validator=maven, downstream=True)
        app = workflow.compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'rollback-test-thread'}}

        app.invoke({'context': env['context'], 'evidence': env['evidence'], 'cache': env['cache']}, config)
        app.invoke(Command(resume='approve'), config)
        result = app.invoke(Command(resume='approve'), config)
        assert result['__interrupt__'][0].value['type'] == 'INTEGRATED_TEST_GENERATION_APPROVAL'

        # Resuming with failing model triggers compensation
        result = app.invoke(Command(resume='approve'), config)
        assert result['status'] == 'STOPPED_PARALLEL_STAGE'
        assert result['recovery'] == 'rolled_back'

        # Confirm whole-set rollback: both files restored to exact baseline
        assert (ws / TASK1_FILE).read_bytes() == orig1
        assert (ws / TASK2_FILE).read_bytes() == orig2
        assert not (ws / TEST_PATH).exists()
