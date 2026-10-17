import copy,json
from pathlib import Path
from unittest.mock import Mock,patch
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.semantic_plan import digest
from governance.release_evidence import facts
from governance.documentation_release import build_workflow,documentation,evaluate
from agents.documentation_agent import generate,validate_selection
from llm.provider import ModelResponse


def manifest(tmp_path):
    return {'workspace':str(tmp_path/'workspace'),'implementation_hash':'source-hash','generated_test_hash':'test-hash',
            'records':{'baseline':{'cases':{'original':'passed'}},'positive':{'cases':{'original':'passed','new':'passed'}},
                       'negative':{'cases':{'original':'passed','new':'failure'}}},
            'approved_implementation_plan_hash':None}


def selection(m):return {section:list(items) for section,items in facts(m).items()}


def state(tmp_path,m):
    output=tmp_path/'output';output.mkdir()
    return {'origin':{},'bundle_hash':digest(m),'run_id':'docs-run','output_dir':str(output),'test_review_hash':digest(m)}


@pytest.mark.parametrize('attack',['unsupported_implementation','unsupported_testing','omit_limitations','free_prose','duplicate'])
def test_unsupported_claims_rejected(attack,tmp_path):
    m=manifest(tmp_path);payload=selection(m)
    if attack=='unsupported_implementation':payload['implementation_notes'].append('LLM_generated_the_Clock_source')
    if attack=='unsupported_testing':payload['testing_evidence'].append('all_security_tests_passed')
    if attack=='omit_limitations':payload.pop('limitations')
    if attack=='free_prose':payload['additional_claim']='Ready for production'
    if attack=='duplicate':payload['testing_evidence'].append(payload['testing_evidence'][0])
    with pytest.raises(ValueError):generate(Mock(invoke=Mock(return_value=ModelResponse(json.dumps(payload)))),m)


def test_document_provenance_preserves_clock_and_test_distinction(tmp_path):
    m=manifest(tmp_path);content,_,prompt,_=generate(Mock(invoke=Mock(return_value=ModelResponse(json.dumps(selection(m))))),m)
    assert b'exact human-reviewed' in content and b'not live AI source generation' in content
    assert b'trusted deterministic renderer' in content and b'four-file engineering plan remains unapproved' in content
    assert 'source-hash' in prompt


@pytest.mark.parametrize('reject',[False,True])
def test_review_interrupt_precedes_documentation_and_blocked_readiness(tmp_path,reject):
    m=manifest(tmp_path);s=state(tmp_path,m);s.pop('test_review_hash')
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps(selection(m)))))
    with patch('governance.documentation_release.verify',return_value=m),patch.object(audit_logger,'DB_PATH',tmp_path/'audit.db'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=build_workflow(model).compile(checkpointer=saver);config={'configurable':{'thread_id':'same-thread'}}
        paused=app.invoke(s,config=config)
        assert paused['__interrupt__'][0].value['type']=='TEST_RESULTS_REVIEW';model.invoke.assert_not_called()
        result=app.invoke(Command(resume='reject' if reject else 'approve'),config=config)
        if reject:
            assert result['status']=='STOPPED_TEST_RESULTS_REJECTED';model.invoke.assert_not_called()
            assert not app.get_state(config).next
        else:
            assert result['__interrupt__'][0].value['type']=='FINAL_RELEASE_REVIEW'
            assert result['readiness']['status']=='BLOCKED'
            assert 'matching_implementation_plan_approved' in result['readiness']['blockers']
            assert result['readiness']['checks']['human_test_results_review']
            stopped=app.invoke(Command(resume='approve'),config=config)
            assert stopped['status']=='STOPPED_RELEASE_BLOCKED'
            assert not stopped['readiness']['deployment_authorized']
        assert app.get_state(config).config['configurable']['thread_id']=='same-thread'


@pytest.mark.parametrize('attack',['existing_doc','changed_bundle','no_test_review','unsupported_claim'])
def test_doc_gate_preserves_existing_docs_and_fails_closed(tmp_path,attack):
    m=manifest(tmp_path);s=state(tmp_path,m);path=Path(s['output_dir'])/'engineering-notes.md'
    if attack=='existing_doc':path.write_text('existing documentation')
    if attack=='changed_bundle':s['bundle_hash']='old'
    if attack=='no_test_review':s['test_review_hash']='missing'
    payload=selection(m)
    if attack=='unsupported_claim':payload['implementation_notes'].append('arbitrary claim')
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps(payload))))
    with patch('governance.documentation_release.verify',return_value=m),patch('governance.documentation_release.log'),patch('governance.documentation_release.audit_approved',return_value=True):
        result=documentation(s,model)
    assert result['status'] in ('STOPPED_DOCUMENTATION_INVALID','DOCUMENTATION_RETRY')
    if attack=='existing_doc':assert path.read_text()=='existing documentation'
    else:assert not path.exists()


@pytest.mark.parametrize('attack',['source_changed','doc_tampered','missing_review','lineage_swapped'])
def test_readiness_independently_rechecks_missing_or_changed_artifacts(tmp_path,attack):
    m=manifest(tmp_path);s=state(tmp_path,m)
    with patch('governance.documentation_release.verify',return_value=m),patch('governance.documentation_release.log'),patch('governance.documentation_release.audit_approved',return_value=True):
        ready=documentation(s,Mock(invoke=Mock(return_value=ModelResponse(json.dumps(selection(m))))))
        if attack=='doc_tampered':Path(ready['document']['path']).write_text('All code was AI generated; release now')
        if attack=='missing_review':ready['test_review_hash']='missing'
        if attack=='lineage_swapped':ready['document']['bundle_hash']='different-workspace'
        if attack=='source_changed':
            with patch('governance.documentation_release.verify',side_effect=PermissionError('Source hash changed')):result=evaluate(ready)
        else:result=evaluate(ready)
    assert result['readiness']['status']=='BLOCKED' and not result['readiness']['deployment_authorized']
    key='human_test_results_review' if attack=='missing_review' else 'artifact_lineage_and_source_integrity' if attack=='source_changed' else 'documentation_verified'
    assert not result['readiness']['checks'][key]


def test_forged_state_flag_without_human_audit_cannot_generate(tmp_path):
    m=manifest(tmp_path);s=state(tmp_path,m);model=Mock()
    with patch('governance.documentation_release.verify',return_value=m),patch.object(audit_logger,'DB_PATH',tmp_path/'absent.db'):
        result=documentation(s,model)
    assert result['status']=='STOPPED_DOCUMENTATION_INVALID';model.invoke.assert_not_called()


def test_missing_approval_receipt_blocks_readiness_even_with_correct_hash(tmp_path):
    m=manifest(tmp_path);s=state(tmp_path,m)
    with patch('governance.documentation_release.verify',return_value=m),patch('governance.documentation_release.log'),patch('governance.documentation_release.audit_approved',return_value=True):
        published=documentation(s,Mock(invoke=Mock(return_value=ModelResponse(json.dumps(selection(m))))))
    with patch('governance.documentation_release.verify',return_value=m),patch('governance.documentation_release.log'),patch('governance.documentation_release.audit_approved',return_value=False):
        result=evaluate(published)
    assert not result['readiness']['checks']['human_test_results_review']
    assert not result['readiness']['checks']['documentation_verified']


def test_documentation_retry_exhaustion_stops_before_readiness(tmp_path):
    m=manifest(tmp_path);s=state(tmp_path,m);s.pop('test_review_hash')
    model=Mock(invoke=Mock(return_value=ModelResponse('{"unsupported":"production ready"}')))
    with patch('governance.documentation_release.verify',return_value=m),patch.object(audit_logger,'DB_PATH',tmp_path/'audit.db'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=build_workflow(model).compile(checkpointer=saver);config={'configurable':{'thread_id':'bounded'}}
        app.invoke(s,config=config);result=app.invoke(Command(resume='approve'),config=config)
        assert result['status']=='STOPPED_DOCUMENTATION_INVALID' and result['documentation_attempts']==3
        assert model.invoke.call_count==3 and not app.get_state(config).next
        assert not (Path(s['output_dir'])/'engineering-notes.md').exists()
        assert len(list(Path(s['output_dir']).glob('rejected-*-response.txt')))==3


def test_evidence_changes_after_interrupt_invalidate_pending_approval(tmp_path):
    m=manifest(tmp_path);s=state(tmp_path,m);s.pop('test_review_hash');model=Mock()
    with patch('governance.documentation_release.verify',return_value=m),patch.object(audit_logger,'DB_PATH',tmp_path/'audit.db'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=build_workflow(model).compile(checkpointer=saver);config={'configurable':{'thread_id':'stale-review'}}
        app.invoke(s,config=config)
        m['implementation_hash']='different implementation'
        result=app.invoke(Command(resume='approve'),config=config)
        assert result['status']=='STOPPED_EVIDENCE_INVALID';model.invoke.assert_not_called()
        assert not app.get_state(config).next
