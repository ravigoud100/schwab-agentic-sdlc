"""Automated tests for unified routing, parallel eligibility, sequential fallback, and approval enforcement."""

import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from governance.task_orchestrator import is_parallel_eligible
from llm.provider import MockProvider
from test_plan_lineage import approved_state
import main
from main import (
    EngineeringState,
    execution_strategy_router,
    workflow,
    initialize_workspace,
)


def _make_task(task_id: str, allowed_file: str, execution_type: str = "implementation", dependencies: list | None = None, status: str = "READY") -> dict:
    return {
        "task_id": task_id,
        "description": f"Implement or test {task_id}",
        "execution_type": execution_type,
        "allowed_files": [allowed_file],
        "dependencies": dependencies or [],
        "status": status,
    }


def test_parallel_eligibility_multi_task_disjoint():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
        _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java"),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is True
    assert "Eligible for dependency-aware parallel execution" in reason


def test_parallel_eligibility_single_task():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "requires 2 to 6 tasks (found 1)" in reason


def test_parallel_eligibility_too_many_tasks():
    tasks = [
        _make_task(f"task-{i}", f"url-shortener/src/main/java/url_shortener/File{i}.java")
        for i in range(7)
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "requires 2 to 6 tasks (found 7)" in reason


def test_parallel_eligibility_overlapping_files():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
        _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlService.java"),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "overlap" in reason.lower()


def test_parallel_eligibility_validation_only():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java", execution_type="validate_only"),
        _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java"),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "non-implementation execution_type" in reason


def test_parallel_eligibility_cyclic_dependency():
    tasks = [
        _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java", dependencies=["task-2"]),
        _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java", dependencies=["task-1"]),
    ]
    eligible, reason = is_parallel_eligible(tasks)
    assert eligible is False
    assert "Circular" in reason


def test_execution_strategy_router_parallel():
    state = {
        "auto_orchestration": True,
        "tasks": [
            _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
            _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java"),
        ],
    }
    assert execution_strategy_router(state) == "parallel"


def test_execution_strategy_router_sequential_default():
    """Default execution strategy for ordinary sequential source-changing workflows is integrated_sdlc."""
    state = {
        "auto_orchestration": True,
        "tasks": [
            _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
        ],
    }
    assert execution_strategy_router(state) == "integrated"


def test_execution_strategy_router_explicit_legacy():
    """Legacy task scheduler is accessible only through explicit legacy option."""
    state = {
        "legacy": True,
        "auto_orchestration": True,
        "tasks": [
            _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
        ],
    }
    assert execution_strategy_router(state) == "legacy"


def test_execution_strategy_router_validation_only_audited_skip(tmp_path, monkeypatch):
    """Validation-only behavior routes to verification/validation with audited skipped stages."""
    db_file = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)
    state = {
        "workflow_id": "val-skip-test",
        "auto_orchestration": True,
        "tasks": [
            _make_task("verify-task", "url-shortener/src/main/java/url_shortener/ShortUrl.java", execution_type="validate_only"),
        ],
    }
    assert execution_strategy_router(state) == "legacy"
    with sqlite3.connect(db_file) as conn:
        events = [r[0] for r in conn.execute("SELECT event FROM audit_events WHERE workflow_id=?", ("val-skip-test",)).fetchall()]
    assert "VALIDATION_ONLY_STAGES_SKIPPED" in events


def test_execution_strategy_router_explicit_flags():
    assert execution_strategy_router({"parallel_evidence": "/tmp/pe"}) == "parallel"
    assert execution_strategy_router({"integrated_sdlc": True, "integration_evidence": "/tmp/ie"}) == "integrated"


def test_execution_strategy_router_disabled_auto_orchestration():
    state = {
        "auto_orchestration": False,
        "tasks": [
            _make_task("task-1", "url-shortener/src/main/java/url_shortener/UrlService.java"),
            _make_task("task-2", "url-shortener/src/main/java/url_shortener/UrlController.java"),
        ],
    }
    # Disabled auto_orchestration defaults to integrated SDLC unless legacy is explicitly set
    assert execution_strategy_router(state) == "integrated"


def test_parallel_stage_reporting_does_not_claim_complete_sdlc(tmp_path, monkeypatch):
    """Parallel execution path does not claim complete SDLC unless tests, docs, and readiness are executed."""
    from main import parallel_node, final_human_review_node
    from langgraph.types import Command
    from unittest.mock import Mock

    db_file = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)

    fake_workflow = Mock()
    fake_compiled = Mock()
    fake_compiled.invoke.return_value = {
        "status": "AWAITING_PARALLEL_RESULTS_REVIEW",
        "transaction": {"paths": ["UrlService.java", "UrlController.java"]},
    }
    fake_workflow.compile.return_value = fake_compiled

    with patch("governance.parallel_execution.build_workflow", return_value=fake_workflow):
        node_out = parallel_node({
            "workspace": str(tmp_path),
            "workflow_id": "parallel-reporting-test",
            "parallel_evidence": str(tmp_path / "pe"),
        })

    assert node_out["complete_sdlc"] is False
    assert node_out["sdlc_scope"] == "parallel_implementation_and_validation"
    assert "additive_test_generation" in node_out["unexecuted_stages"]
    assert "documentation_generation" in node_out["unexecuted_stages"]
    assert "release_readiness_evaluation" in node_out["unexecuted_stages"]

    # Verify final human review incorporates the parallel reporting disclaimer
    review_state = {
        "workflow_id": "parallel-reporting-test",
        "workspace": str(tmp_path),
        "requirement": "Parallel feature",
        "tasks": [],
        "parallel_result": node_out["parallel_result"],
        "source_files_changed": node_out["source_files_changed"],
        "validation_result": {"passed": True, "exit_code": 0},
    }
    with patch("main.compute_final_review_bundle", return_value={"bundle_hash": "b1", "source_hashes": {}}):
        try:
            final_human_review_node(review_state)
        except Exception as e:
            # LangGraph interrupt raises GraphInterrupt
            payload = getattr(e, "args", [{}])[0]
            if isinstance(payload, list) and payload:
                val = payload[0].value
                assert val.get("complete_sdlc") is False
                assert val.get("sdlc_scope") == "parallel_implementation_and_validation"
                assert "additive_test_generation" in val.get("unexecuted_stages", [])


def test_plan_rejection_stops_execution(tmp_path, monkeypatch):
    db_file = tmp_path / "audit.db"
    wf_db = tmp_path / "workflow.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_file)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("MOCK_SCENARIO", "url-expiration")

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    source = repo / main.IMPLEMENTATION_RELATIVE_PATH
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("package url_shortener; public class ShortUrl {}\n")

    provider = MockProvider("url-expiration")

    with patch("main.model", provider), \
         patch("agents.codebase_agent.get_model", return_value=provider), \
         SqliteSaver.from_conn_string(str(wf_db)) as checkpointer:

        app = workflow.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "reject-flow"}}

        state = {
            "workspace": str(repo),
            "workflow_id": "reject-flow",
            "requirement": "Verify existing URL expiration",
            "requirement_version": 1,
            "status": "STARTED",
            "approval": "",
            "plan": "",
            "tasks": [],
            "replan_history": [],
            "attempt_history": [],
            "validation_result": {},
            "retry_count": 0,
            "implementation_changed": False,
            "repair_applied": False,
            "repair_rolled_back": False,
            "repair_rollback_blocked": False,
            "final_human_review_required": True,
            "engineering_accepted": False,
            "production_release_authorized": False,
        }

        # Pauses at plan approval
        paused = app.invoke(state, config=config)
        assert "__interrupt__" in paused

        # Resume with reject
        result = app.invoke(Command(resume="reject"), config=config)
        assert result["status"] == "STOPPED"
        assert result["approval"] == "reject"

    # Verify audit event recorded
    with sqlite3.connect(db_file) as conn:
        events = conn.execute(
            "SELECT event FROM audit_events WHERE workflow_id=?",
            ("reject-flow",)
        ).fetchall()
    event_names = [e[0] for e in events]
    assert "PLAN_REJECTED" in event_names


def test_sequential_workflow_completes_documentation_and_readiness_before_final_acceptance(approved_state, tmp_path, tmp_path_factory, monkeypatch):
    """End-to-end test proving a standard sequential workflow completes documentation and readiness before final review."""
    from governance import integrated_sdlc as stage
    from governance.semantic_plan import digest
    from test_plan_lineage import SOURCE, TEST
    from llm.provider import ModelResponse
    from unittest.mock import Mock

    s = approved_state
    plan = json.loads(s['plan'])
    plan['test_stage']['allowed_new_test_files'] = [stage.TEST_PATH]
    s['plan'] = json.dumps(plan)
    s['semantic_plan_hash'] = digest(s['plan'])
    evidence_dir = tmp_path_factory.mktemp('integrated-evidence')
    s.update({
        'approval': '',
        'approved_task_hash': '',
        'integrated_sdlc': True,
        'implementation_response_format': 'edits',
        'integration_evidence': str(evidence_dir),
        'integration_cache': str(tmp_path / 'cache'),
    })

    maven_calls = []
    def fake_maven(root, evidence, phase, cache):
        maven_calls.append(phase)
        out = Path(evidence) / phase
        out.mkdir(parents=True, exist_ok=True)
        cases = {'fixture.Original#original': 'passed'}
        if phase == 'positive':
            cases.update({
                'url_shortener.GeneratedExpirationBoundaryTest#' + n: 'passed'
                for n in ('futureExpiration', 'pastExpiration', 'equalNowExpiration', 'missingExpiration')
            })
        xml = '<testsuite>' + ''.join(f'<testcase classname="{k.split("#")[0]}" name="{k.split("#")[1]}" />' for k in cases) + '</testsuite>'
        (out / 'TEST-fixture.xml').write_text(xml)
        (out / 'maven.log').write_text('Synthetic Maven execution')
        record = {'exit_code': 0, 'cases': cases, 'command': ['./mvnw', '-o', '-Dmaven.repo.local=/fixture', 'clean', 'test']}
        (out / 'result.json').write_text(json.dumps(record))
        return record

    monkeypatch.setattr(stage, 'maven', fake_maven)

    model_stages = []
    def fake_invoke(prompt, *, stage=None):
        model_stages.append(stage)
        if stage == 'implementation':
            return ModelResponse(json.dumps({'edits': [{'path': SOURCE, 'old_text': 'original', 'new_text': 'correct'}]}))
        if stage == 'test_generation':
            return ModelResponse(json.dumps({'cases': [{'obligation_id': k, 'spec': v} for k, v in __import__('agents.test_generation_agent', fromlist=['CASES']).CASES.items()]}))
        if stage == 'documentation':
            return ModelResponse(prompt.split('Return this exact JSON shape, with every ID required (ordering may change):\n')[1].strip())
        raise AssertionError(f"Unexpected model stage: {stage}")

    mock_model = Mock(invoke=Mock(side_effect=fake_invoke))
    monkeypatch.setattr(main, 'model', mock_model)

    wf_db = tmp_path / 'graph.db'
    with SqliteSaver.from_conn_string(str(wf_db)) as saver:
        app = main.workflow.compile(checkpointer=saver)
        config = {'configurable': {'thread_id': s['workflow_id']}}
        app.update_state(config, s, as_node='planning')

        # 1. Plan approval interrupt
        res1 = app.invoke(None, config)
        assert 'plan' in res1['__interrupt__'][0].value

        # 2. Approve plan -> routes to integrated_sdlc -> runs entry, implementation, source_validation
        res2 = app.invoke(Command(resume='approve'), config)
        assert res2['__interrupt__'][0].value['type'] == 'INTEGRATED_TEST_GENERATION_APPROVAL'
        assert 'implementation' in model_stages
        assert 'documentation' not in model_stages

        # 3. Approve test generation -> runs test_generation, maven_validation -> pauses at test results review
        res3 = app.invoke(Command(resume='approve'), config)
        assert res3['__interrupt__'][0].value['type'] == 'TEST_RESULTS_REVIEW'
        assert 'test_generation' in model_stages
        assert 'documentation' not in model_stages

        # 4. Approve test results review -> runs documentation (generates engineering-notes.md), then readiness (evaluates checks) -> pauses at final review!
        res4 = app.invoke(Command(resume='approve'), config)
        assert res4['__interrupt__'][0].value['type'] == 'FINAL_RELEASE_REVIEW'

        # PROVE documentation and readiness completed before final engineering review:
        assert 'documentation' in model_stages
        doc_file = Path(evidence_dir) / 'engineering-notes.md'
        assert doc_file.exists(), "engineering-notes.md must exist before final review"
        doc_content = doc_file.read_text()
        assert len(doc_content) > 0

        # PROVE readiness evaluation completed before final review:
        report = res4['__interrupt__'][0].value['report']
        assert 'checks' in report
        assert report['checks']['documentation_verified'] is True
        assert report['checks']['matching_implementation_plan_approved'] is True
        assert report['checks']['human_test_results_review'] is True
        # Release authorization remains false
        assert report['checks']['release_security_and_operational_signoff'] is False

        # 5. Final review decision
        res5 = app.invoke(Command(resume='approve'), config)
        assert res5['status'] in ('STOPPED_RELEASE_BLOCKED', 'FINAL_REVIEW_RECORDED')
