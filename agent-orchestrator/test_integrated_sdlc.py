"""Controlled graph integration tests; synthetic Maven is never live evidence."""
import copy
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
import main
from governance import integrated_sdlc as stage
from governance.semantic_plan import digest
from governance.release_evidence import facts
from governance.test_safety import sha
from llm.provider import ModelResponse
from test_plan_lineage import approved_state, SOURCE, TEST


@pytest.fixture
def setup(approved_state,tmp_path,tmp_path_factory,monkeypatch):
    s=approved_state
    plan=json.loads(s['plan']);plan['test_stage']['allowed_new_test_files']=[stage.TEST_PATH]
    s['plan']=json.dumps(plan);s['semantic_plan_hash']=digest(s['plan'])
    s.update({'approval':'','approved_task_hash':'','integrated_sdlc':True,'implementation_response_format':'edits',
        'integration_evidence':str(tmp_path_factory.mktemp('integrated-evidence')),'integration_cache':str(tmp_path/'cache')})
    calls=[]
    def maven(root,evidence,phase,cache):
        calls.append(phase);out=Path(evidence)/phase;out.mkdir()
        cases={'fixture.Original#original':'passed'}
        if phase=='positive':cases.update({'url_shortener.GeneratedExpirationBoundaryTest#'+n:'passed' for n in ('futureExpiration','pastExpiration','equalNowExpiration','missingExpiration')})
        xml='<testsuite>'+''.join(f'<testcase classname="{k.split("#")[0]}" name="{k.split("#")[1]}" />' for k in cases)+'</testsuite>'
        (out/'TEST-fixture.xml').write_text(xml);(out/'maven.log').write_text('Controlled synthetic execution, not real Maven')
        record={'exit_code':0,'cases':cases,'command':['./mvnw','-o','-Dmaven.repo.local=/fixture','clean','test']}
        (out/'result.json').write_text(json.dumps(record));return record
    monkeypatch.setattr(stage,'maven',maven)
    def invoke(prompt,*,stage):
        if stage=='implementation':return ModelResponse(json.dumps({'edits':[{'path':SOURCE,'old_text':'original','new_text':'correct'}]}))
        if stage=='test_generation':return ModelResponse(json.dumps({'cases':[{'obligation_id':k,'spec':v} for k,v in __import__('agents.test_generation_agent',fromlist=['CASES']).CASES.items()]}))
        if stage=='documentation':
            # Exact schema supplied by the production evidence-only prompt.
            return ModelResponse(prompt.split('Return this exact JSON shape, with every ID required (ordering may change):\n')[1].strip())
        raise AssertionError(stage)
    model=Mock(invoke=Mock(side_effect=invoke));monkeypatch.setattr(main,'model',model)
    return s,model,calls


def start(s,saver):
    app=main.workflow.compile(checkpointer=saver);config={'configurable':{'thread_id':s['workflow_id']}}
    app.update_state(config,s,as_node='planning')
    return app,config,app.invoke(None,config)


def test_one_thread_plan_to_test_review_docs_and_blocked_final_review(setup,tmp_path):
    s,model,calls=setup;original=(Path(s['workspace'])/TEST).read_bytes()
    with SqliteSaver.from_conn_string(str(tmp_path/'graph.db')) as saver:
        app,config,result=start(s,saver)
        assert 'plan' in result['__interrupt__'][0].value
        assert calls==[] and not model.invoke.called
        result=app.invoke(Command(resume='approve'),config)
        assert result['__interrupt__'][0].value['type']=='INTEGRATED_TEST_GENERATION_APPROVAL'
        assert calls==['baseline','implementation-1']
        assert (Path(s['workspace'])/SOURCE).read_text()=='correct'
        assert not (Path(s['workspace'])/stage.TEST_PATH).exists()
        result=app.invoke(Command(resume='approve'),config)
        assert result['__interrupt__'][0].value['type']=='TEST_RESULTS_REVIEW'
        assert calls==['baseline','implementation-1','positive']
        assert not any(c.kwargs['stage']=='documentation' for c in model.invoke.call_args_list)
        result=app.invoke(Command(resume='approve'),config)
        assert result['__interrupt__'][0].value['type']=='FINAL_RELEASE_REVIEW'
        report=result['__interrupt__'][0].value['report']
        assert report['status']=='BLOCKED' and report['checks']['matching_implementation_plan_approved']
        assert not report['checks']['release_security_and_operational_signoff']
        result=app.invoke(Command(resume='reject'),config)
        assert result['status']=='STOPPED_RELEASE_BLOCKED' and not app.get_state(config).next
        integrated=result['integrated_result']
        assert integrated['origin']['source_transaction']==integrated['transaction']['transaction_id']
        assert integrated['run_id']==s['workflow_id']
        assert stage.verify(integrated['origin'])['approved_implementation_plan_hash']==digest(s['plan'])
        assert (Path(s['workspace'])/TEST).read_bytes()==original
        assert [c.kwargs['stage'] for c in model.invoke.call_args_list]==['implementation','test_generation','documentation']
    with sqlite3.connect(s['plan_authorization_audit_db'] if s.get('plan_authorization_audit_db') else tmp_path/'audit.db') as c:
        rows=[r[0] for r in c.execute('select event from audit_events order by id')]
    sequence=['ENGINEERING_PLAN_AUTHORIZED','TRANSACTION_APPLIED','ENGINEERING_EXECUTION_BOUND',
        'TEST_SCOPE_APPROVED','TEST_FILE_CREATED','TEST_STAGE_EXIT_PASSED','ENGINEERING_TESTS_BOUND',
        'TEST_RESULTS_APPROVED','DOCUMENTATION_GATE_PASSED','RELEASE_READINESS_EVALUATED','FINAL_RELEASE_REVIEW_RECORDED']
    assert [rows.index(e) for e in sequence]==sorted(rows.index(e) for e in sequence)


@pytest.mark.parametrize('review',['plan','generation','results'])
def test_rejection_never_skips_human_gate(setup,tmp_path,review):
    s,model,calls=setup
    with SqliteSaver.from_conn_string(str(tmp_path/'graph.db')) as saver:
        app,config,_=start(s,saver)
        result=app.invoke(Command(resume='reject' if review=='plan' else 'approve'),config)
        if review!='plan':result=app.invoke(Command(resume='reject' if review=='generation' else 'approve'),config)
        if review=='results':result=app.invoke(Command(resume='reject'),config)
        assert not app.get_state(config).next
    assert not any(c.kwargs['stage']=='documentation' for c in model.invoke.call_args_list)
    if review in {'plan','generation'}:
        assert (Path(s['workspace'])/SOURCE).read_text()=='original'
        assert not (Path(s['workspace'])/stage.TEST_PATH).exists()


def test_failed_combined_validation_restores_source_and_additive_test(setup,tmp_path,monkeypatch):
    s,model,calls=setup;real=stage.maven
    def fail(*args):
        result=real(*args)
        if args[2]=='positive':result['exit_code']=1
        return result
    monkeypatch.setattr(stage,'maven',fail)
    with SqliteSaver.from_conn_string(str(tmp_path/'graph.db')) as saver:
        app,config,_=start(s,saver);app.invoke(Command(resume='approve'),config)
        result=app.invoke(Command(resume='approve'),config)
        assert result['status']=='STOPPED_INTEGRATED_STAGE'
        assert result['integrated_result']['recovery']=='rolled_back'
        assert not app.get_state(config).next
    assert (Path(s['workspace'])/SOURCE).read_text()=='original'
    assert not (Path(s['workspace'])/stage.TEST_PATH).exists()


def test_revised_recovery_requires_new_prospective_receipt(setup):
    s,_,_=setup
    s.update({'recovery_approved':True,'retry_count':0,'recovery_candidate_tasks':copy.deepcopy(s['tasks'])})
    s['recovery_candidate_tasks'][0]['description']='Materially different behavior'
    result=main.recovery_retry_node(s)
    assert result['status']=='STOPPED_RECOVERY_REAUTHORIZATION_REQUIRED'
    assert result['plan_authorization']=={} and result['approved_task_hash']==''


@pytest.mark.parametrize('fault',['arbitrary_java','missing_case','publication','external_test','build_change','xml_tamper'])
def test_test_stage_fails_closed_and_compensates_when_safe(setup,tmp_path,monkeypatch,fault):
    s,model,_=setup;invoke=model.invoke.side_effect;run_maven=stage.maven
    if fault in {'arbitrary_java','missing_case'}:
        def bad(prompt,*,stage):
            if stage=='test_generation':return ModelResponse(json.dumps({'java':'disable tests'} if fault=='arbitrary_java' else {'cases':[]}))
            return invoke(prompt,stage=stage)
        model.invoke.side_effect=bad
    if fault=='publication':
        monkeypatch.setattr(stage.os,'link',Mock(side_effect=OSError('publication failure')))
    if fault in {'external_test','build_change','xml_tamper'}:
        def changed(*args):
            result=run_maven(*args)
            if args[2]=='positive':
                if fault=='external_test':(Path(s['workspace'])/stage.TEST_PATH).write_text('external writer')
                elif fault=='build_change':(Path(s['workspace'])/'url-shortener/pom.xml').write_text('unauthorized exclusions')
                else:(Path(args[1])/'positive/TEST-fixture.xml').write_text('<testsuite/>')
            return result
        monkeypatch.setattr(stage,'maven',changed)
    with SqliteSaver.from_conn_string(str(tmp_path/'graph.db')) as saver:
        app,config,_=start(s,saver);app.invoke(Command(resume='approve'),config)
        result=app.invoke(Command(resume='approve'),config)
        assert result['status']=='STOPPED_INTEGRATED_STAGE' and not app.get_state(config).next
    assert not any(c.kwargs['stage']=='documentation' for c in model.invoke.call_args_list)
    if fault=='external_test':
        assert result['integrated_result']['recovery'].startswith('blocked:')
        assert (Path(s['workspace'])/stage.TEST_PATH).read_text()=='external writer'
    else:
        assert result['integrated_result']['recovery']=='rolled_back'
        assert (Path(s['workspace'])/SOURCE).read_text()=='original'
        assert not (Path(s['workspace'])/stage.TEST_PATH).exists()


@pytest.mark.parametrize('changed',['journal_status','production','test','foreign_transaction'])
def test_pending_test_approval_rechecks_same_execution(setup,tmp_path,changed):
    s,_,_=setup
    with SqliteSaver.from_conn_string(str(tmp_path/'graph.db')) as saver:
        app,config,_=start(s,saver);app.invoke(Command(resume='approve'),config)
        if changed=='production':(Path(s['workspace'])/SOURCE).write_text('external source')
        elif changed=='test':(Path(s['workspace'])/TEST).write_text('external test')
        else:
            # Exercise evidence predicates directly; graph checkpoint mutation is privileged.
            tx=list((Path(s['workspace'])/'.agent-transactions').glob('*.json'))[0]
            record=json.loads(tx.read_text())
            if changed=='journal_status':record['status']='rolled_back'
            else:record['workflow_id']='foreign'
            tx.write_text(json.dumps(record))
        result=app.invoke(Command(resume='approve'),config)
        assert result['status']=='STOPPED_INTEGRATED_STAGE' and not app.get_state(config).next
        assert not (Path(s['workspace'])/stage.TEST_PATH).exists()
    if changed=='production':assert (Path(s['workspace'])/SOURCE).read_text()=='external source'
    if changed=='test':assert (Path(s['workspace'])/TEST).read_text()=='external test'


def test_sqlite_resume_reconstructs_subgraph_without_reapplying_source(setup,tmp_path):
    s,model,calls=setup;database=str(tmp_path/'graph.db')
    with SqliteSaver.from_conn_string(database) as saver:
        app,config,_=start(s,saver);app.invoke(Command(resume='approve'),config)
        journals=list((Path(s['workspace'])/'.agent-transactions').glob('*.json'))
        assert len(journals)==1
    with SqliteSaver.from_conn_string(database) as saver:
        app=main.workflow.compile(checkpointer=saver)
        result=app.invoke(Command(resume='approve'),config)
        assert result['__interrupt__'][0].value['type']=='TEST_RESULTS_REVIEW'
    with SqliteSaver.from_conn_string(database) as saver:
        app=main.workflow.compile(checkpointer=saver)
        result=app.invoke(Command(resume='approve'),config)
        assert result['__interrupt__'][0].value['type']=='FINAL_RELEASE_REVIEW'
    assert calls==['baseline','implementation-1','positive']
    assert len(list((Path(s['workspace'])/'.agent-transactions').glob('*.json')))==1
    assert sum(c.kwargs['stage']=='implementation' for c in model.invoke.call_args_list)==1


def test_source_compilation_failure_automatically_retries_same_plan(setup,tmp_path,monkeypatch):
    s,model,calls=setup;real=stage.maven
    def once(*args):
        record=real(*args)
        if args[2]=='implementation-1':
            record['exit_code']=1;record['cases']={}
            directory=Path(args[1])/args[2]
            (directory/'TEST-fixture.xml').unlink()
            # Compilation has no Surefire XML; preserve the failed compiler log.
            (directory/'maven.log').write_text('[ERROR] compilation failure: seeded controlled fixture')
            (directory/'result.json').write_text(json.dumps(record))
        return record
    monkeypatch.setattr(stage,'maven',once)
    with SqliteSaver.from_conn_string(str(tmp_path/'graph.db')) as saver:
        app,config,_=start(s,saver);result=app.invoke(Command(resume='approve'),config)
        assert result['__interrupt__'][0].value['type']=='INTEGRATED_TEST_GENERATION_APPROVAL'
        assert calls==['baseline','implementation-1','implementation-2']
        implementation_calls=[c for c in model.invoke.call_args_list if c.kwargs['stage']=='implementation']
        assert len(implementation_calls)==2 and 'seeded controlled fixture' in implementation_calls[1].args[0]
        assert 'Original sources after rollback' in implementation_calls[1].args[0]
        result=app.invoke(Command(resume='approve'),config)
        assert result['__interrupt__'][0].value['type']=='TEST_RESULTS_REVIEW'


def test_source_gate_retry_exhaustion_restores_every_attempt(setup,tmp_path,monkeypatch):
    s,model,calls=setup;real=stage.maven
    def always_fail(*args):
        record=real(*args)
        if args[2].startswith('implementation-'):record['exit_code']=1
        return record
    monkeypatch.setattr(stage,'maven',always_fail)
    with SqliteSaver.from_conn_string(str(tmp_path/'graph.db')) as saver:
        app,config,_=start(s,saver);result=app.invoke(Command(resume='approve'),config)
        assert result['status']=='STOPPED_INTEGRATED_STAGE' and not app.get_state(config).next
    assert sum(c.kwargs['stage']=='implementation' for c in model.invoke.call_args_list)==3
    assert (Path(s['workspace'])/SOURCE).read_text()=='original'
    assert not (Path(s['workspace'])/stage.TEST_PATH).exists()
    journals=[json.loads(p.read_text()) for p in (Path(s['workspace'])/'.agent-transactions').glob('*.json')]
    assert len(journals)==3 and all(j['status']=='rolled_back' for j in journals)


def test_source_gate_launch_failure_restores_pending_transaction(setup,tmp_path,monkeypatch):
    s,_,_=setup;real=stage.maven
    def launch(*args):
        if args[2].startswith('implementation-'):raise OSError('Maven unavailable')
        return real(*args)
    monkeypatch.setattr(stage,'maven',launch)
    with SqliteSaver.from_conn_string(str(tmp_path/'graph.db')) as saver:
        app,config,_=start(s,saver);result=app.invoke(Command(resume='approve'),config)
        assert result['integrated_result']['recovery']=='rolled_back'
        assert not app.get_state(config).next
    assert (Path(s['workspace'])/SOURCE).read_text()=='original'
