"""Prepare and resume a live AI implementation acceptance run.

Run `prepare DIRECTORY`, review plan.json, then `resume DIRECTORY --decision approve`.
Use --force-failure on resume to exercise genuine Maven failure and rollback.
Artifacts, Git snapshot, workspace, SQLite records and logs remain in DIRECTORY.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

os.environ["LLM_PROVIDER"] = "ollama"
os.environ["OLLAMA_FORMAT"] = "json"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.workspace_manager import initialize_workspace
from governance.task_orchestrator import validate_task_plan
import main

REQUIREMENT = (
    "Add public boolean hasReachedClickLimit(long maxClicks) to the existing ShortUrl entity. "
    "Throw IllegalArgumentException when maxClicks is zero or negative. Otherwise return "
    "true when the existing click count is greater than or equal to maxClicks, false otherwise. "
    "Preserve all existing behavior. This method does not exist yet. Modify only "
    "url-shortener/src/main/java/url_shortener/ShortUrl.java in one implementation task. "
    "The demonstration already supplies independent acceptance tests; do not modify tests."
)
RELATIVE = main.IMPLEMENTATION_RELATIVE_PATH
APPROVED_PATHS = [RELATIVE]
FEATURE_MARKER = "hasReachedClickLimit"
BASELINE_MARKER = "hasReachedClickLimit"
TEST_RELATIVE = "url-shortener/src/test/java/url_shortener/ClickLimitAcceptanceTest.java"
TEST = '''package url_shortener;
import org.junit.jupiter.api.Test;
import java.lang.reflect.InvocationTargetException;
import static org.junit.jupiter.api.Assertions.*;
class ClickLimitAcceptanceTest {
    private boolean reached(ShortUrl url, long limit) throws Exception {
        return (boolean) ShortUrl.class.getMethod("hasReachedClickLimit", long.class).invoke(url, limit);
    }
    @Test void checksThresholdAndPreservesClicks() throws Exception {
        ShortUrl url = new ShortUrl("https://example.com", "demo");
        assertFalse(reached(url, 1));
        url.incrementClickCount();
        assertTrue(reached(url, 1));
        assertFalse(reached(url, 2));
        url.incrementClickCount();
        assertTrue(reached(url, 1));
        assertTrue(reached(url, 2));
        assertEquals(2L, url.getClickCount());
    }
    @Test void rejectsInvalidLimits() throws Exception {
        ShortUrl url = new ShortUrl();
        for (long limit : new long[] {0, -1, Long.MIN_VALUE}) {
            InvocationTargetException error = assertThrows(InvocationTargetException.class, () -> reached(url, limit));
            assertInstanceOf(IllegalArgumentException.class, error.getCause());
        }
    }
    @Test void optionalFailureProbe() {
        assertFalse(Boolean.getBoolean("demo.forceValidationFailure"), "Deliberate rollback validation probe");
    }
}
'''


def git(repo, *args):
    subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True)


def configure(directory):
    audit_logger.DB_PATH = directory / 'audit.db'
    cache = directory / 'maven-cache'
    os.environ['MAVEN_ARGS'] = f'-o -Dmaven.repo.local={cache}'
    os.environ['LLM_PROVIDER'] = 'ollama'


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=False)
    configure(directory)
    root = Path(__file__).resolve().parents[2]
    repo = directory / 'snapshot'
    repo.mkdir()
    shutil.copytree(root / 'url-shortener', repo / 'url-shortener',
                    ignore=shutil.ignore_patterns('.git', 'target', '.jdk-25', '.env'))
    target = repo / RELATIVE
    if FEATURE_MARKER in target.read_text():
        raise RuntimeError('Requirement already implemented; choose a new scenario')
    (repo / TEST_RELATIVE).write_text(TEST)
    git(repo, 'init')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Acceptance Demo', '-c', 'user.email=demo@example.com', 'commit', '-m', 'Baseline with independent acceptance tests')
    shutil.copytree(Path.home() / '.m2/repository', directory / 'maven-cache', ignore=shutil.ignore_patterns('.locks'))
    workflow_id = str(uuid.uuid4())
    workspace = initialize_workspace(workflow_id, repo)
    baseline = main.validate_implementation(workspace)
    (directory / 'baseline.json').write_text(json.dumps(baseline, indent=2))
    if baseline['passed'] or BASELINE_MARKER not in baseline['output']:
        raise RuntimeError('Baseline must fail specifically because the required method is absent')
    metadata = {'workflow_id': workflow_id, 'workspace': str(workspace)}
    (directory / 'run.json').write_text(json.dumps(metadata, indent=2))
    audit_logger.log_event(workflow_id, 'live_demo', 'WORKFLOW_STARTED', {'workspace': str(workspace), 'baseline_passed': False})
    state = {**metadata, 'requirement': REQUIREMENT, 'approval': '', 'retry_count': 0, 'recovery_enabled': False,
             'implementation_changed': False, 'repair_applied': False}
    with SqliteSaver.from_conn_string(str(directory / 'workflow.db')) as saver:
        app = main.workflow.compile(checkpointer=saver)
        result = app.invoke(state, config={'configurable': {'thread_id': workflow_id}})
    export_plan(directory, result)


def export_plan(directory, result):
    if '__interrupt__' not in result:
        raise RuntimeError('Workflow did not pause for human approval')
    plan = json.loads(result['plan'])
    validate_task_plan(result['tasks'])
    if len(result['tasks']) != 1 or set(result['tasks'][0]['allowed_files']) != set(APPROVED_PATHS) or result['tasks'][0]['execution_type'] != 'implementation':
        raise RuntimeError('Live plan exceeded the approved scenario file scope')
    (directory / 'plan.json').write_text(json.dumps(plan, indent=2))
    (directory / 'analysis.txt').write_text(result['analysis'])
    audit_logger.log_event(result['workflow_id'], 'live_demo', 'PLAN_READY_FOR_APPROVAL',
                           {'task_ids': [task['task_id'] for task in result['tasks']]})
    print('AWAITING HUMAN APPROVAL:', directory / 'plan.json', flush=True)


def replan(directory):
    configure(directory)
    metadata = json.loads((directory / 'run.json').read_text())
    config = {'configurable': {'thread_id': metadata['workflow_id']}}
    with SqliteSaver.from_conn_string(str(directory / 'workflow.db')) as saver:
        app = main.workflow.compile(checkpointer=saver)
        state = app.get_state(config).values
        update = main.planning_agent(state)
        app.update_state(config, update, as_node='planning')
        result = app.invoke(None, config=config)
    export_plan(directory, result)


def fork(source, directory):
    """Reuse the reviewed live plan and analysis in a fresh baseline workspace."""
    directory.mkdir(parents=True, exist_ok=False)
    source_metadata = json.loads((source / 'run.json').read_text())
    with SqliteSaver.from_conn_string(str(source / 'workflow.db')) as saver:
        source_app = main.workflow.compile(checkpointer=saver)
        saved = source_app.get_state({'configurable': {'thread_id': source_metadata['workflow_id']}}).values
    repo = directory / 'snapshot'
    repo.mkdir()
    shutil.copytree(source / 'snapshot/url-shortener', repo / 'url-shortener',
                    ignore=shutil.ignore_patterns('target', '.git', '.jdk-25'))
    (repo / TEST_RELATIVE).write_text(TEST)
    if FEATURE_MARKER in (repo / RELATIVE).read_text():
        raise RuntimeError('Fork must start from the unmodified snapshot')
    git(repo, 'init')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Acceptance Demo', '-c', 'user.email=demo@example.com',
        'commit', '-m', 'Independent rollback baseline')
    shutil.copytree(Path.home() / '.m2/repository', directory / 'maven-cache',
                    ignore=shutil.ignore_patterns('.locks'))
    metadata = {'workflow_id': str(uuid.uuid4()), 'source_workflow_id': source_metadata['workflow_id']}
    metadata['workspace'] = str(initialize_workspace(metadata['workflow_id'], repo))
    (directory / 'run.json').write_text(json.dumps(metadata, indent=2))
    plan = (source / 'plan.json').read_text()
    tasks = main.tasks_from_plan(plan, RELATIVE, strict=True)
    state = {**metadata, 'requirement': saved['requirement'], 'analysis': saved['analysis'],
             'codebase_analysis': saved['codebase_analysis'], 'plan': plan, 'tasks': tasks,
             'approval': '', 'retry_count': 0, 'recovery_enabled': False, 'implementation_changed': False,
             'repair_applied': False, 'plan_version': 1,
             'previous_validation_result': saved.get('validation_result') or saved.get('previous_validation_result', {}),
             'validation_result': {}, 'previous_implementation_error': saved.get('implementation_error', ''),
             'previous_implementation_proposal': json.loads((source / 'implementation-response.json').read_text())
             if (source / 'implementation-response.json').exists() else {}}
    configure(directory)
    audit_logger.log_event(metadata['workflow_id'], 'live_demo', 'WORKFLOW_STARTED',
                           {'source_workflow_id': source_metadata['workflow_id'], 'reused_live_plan': True})
    with SqliteSaver.from_conn_string(str(directory / 'workflow.db')) as saver:
        app = main.workflow.compile(checkpointer=saver)
        config = {'configurable': {'thread_id': metadata['workflow_id']}}
        app.update_state(config, state, as_node='planning')
        result = app.invoke(None, config=config)
    export_plan(directory, result)


def resume(directory, decision, force_failure):
    configure(directory)
    if force_failure:
        os.environ['MAVEN_ARGS'] += ' -Ddemo.forceValidationFailure=true'
    metadata = json.loads((directory / 'run.json').read_text())
    workspace = Path(metadata['workspace'])
    def hashes():
        return {p.relative_to(workspace).as_posix(): main.file_hash(p.read_bytes())
                for p in workspace.rglob('*') if p.is_file() and 'target' not in p.relative_to(workspace).parts and '.git' not in p.relative_to(workspace).parts and '.agent-transactions' not in p.relative_to(workspace).parts}
    from agents import implementation_agent
    live_model = implementation_agent.model
    class RecordingModel:
        def invoke(self, prompt, **kwargs):
            response = live_model.invoke(prompt, **kwargs)
            (directory / 'implementation-response.json').write_text(response.text)
            return response
    implementation_agent.model = RecordingModel()
    before = hashes()
    with SqliteSaver.from_conn_string(str(directory / 'workflow.db')) as saver:
        app = main.workflow.compile(checkpointer=saver)
        result = app.invoke(Command(resume=decision), config={'configurable': {'thread_id': metadata['workflow_id']}})
    after = hashes()
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    summary = {'workflow_id': metadata['workflow_id'], 'status': result['status'],
               'validation': result.get('validation_result'), 'changed_files': changed,
               'forced_failure': force_failure, 'implementation_model': os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:latest')}
    (directory / 'result.json').write_text(json.dumps(summary, indent=2))
    audit_logger.log_event(metadata['workflow_id'], 'live_demo', 'WORKFLOW_FINISHED', summary)
    for checkpoint in result.get('implementation_transaction', {}).get('checkpoints', []):
        evidence = directory / 'sources' / checkpoint['path']
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.with_suffix('.original.java').write_bytes(checkpoint['original_content'])
        evidence.write_bytes((workspace / checkpoint['path']).read_bytes())
    if result.get('rollback_checkpoint'):
        (directory / 'original.java').write_bytes(result['rollback_checkpoint']['original_content'])
    (directory / 'current.java').write_bytes((workspace / RELATIVE).read_bytes())
    print(json.dumps(summary, indent=2), flush=True)
    if decision == 'approve':
        if force_failure:
            assert result['status'] == 'STOPPED_ROLLED_BACK' and not changed
        else:
            assert result['status'] == 'AWAITING_FINAL_HUMAN_REVIEW'
            assert result['validation_result']['passed']
            assert changed == sorted(APPROVED_PATHS)


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'replan', 'fork', 'resume'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--decision', choices=['approve', 'reject'])
    parser.add_argument('--force-failure', action='store_true')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.directory.resolve())
    elif args.action == 'fork':
        if not args.source:
            parser.error('fork requires --source')
        fork(args.source.resolve(), args.directory.resolve())
    elif args.action == 'replan':
        replan(args.directory.resolve())
    else:
        if not args.decision:
            parser.error('resume requires an explicit human --decision')
        resume(args.directory.resolve(), args.decision, args.force_failure)


if __name__ == "__main__":
    cli()
