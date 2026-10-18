"""Synthetic approvals/validation exercise adapter contracts, not live SDLC claims."""
import copy,json,sqlite3,shutil
from pathlib import Path
from unittest.mock import Mock,patch
import pytest
from governance import audit_logger
from governance.audit_logger import log_event
from governance.requirements import requirements_hash
from governance.design import design_binding
from governance.semantic_plan import digest
from governance.task_orchestrator import tasks_from_plan,task_definition_hash
from governance.test_safety import sha
from governance.file_transaction import finish_transaction
from governance.plan_lineage import authorize,before_execution,bind_execution,link_tests,verify_test_link
from agents.implementation_agent import implement_task
from llm.provider import ModelResponse
from test_support import requirements_payload
from test_design_stage import state as workspace_state,artifact,QUESTION,SOURCE,TEST
from test_semantic_plan import plan

NEW='url-shortener/src/test/java/url_shortener/NewBoundaryTest.java'


@pytest.fixture
def approved_state(tmp_path,monkeypatch):
    s=workspace_state(tmp_path);req=requirements_payload('Clock testing',[QUESTION]);answers={'A1':'Custom expiration timestamp'}
    s.update({'structured_requirements':req,'requirements_valid':True,'clarification_complete':True,
      'requirements_source_hash':sha(s['requirement'].encode()),'requirements_hash':requirements_hash(req,answers),
      'clarification_answers':answers,'design_artifact':artifact(s['obligation_contract']),'design_valid':True,'design_version':1})
    # Refresh contract approval because requirements identity changed.
    from governance.semantic_plan import binding
    s['approved_contract_hash']=binding(s);s['design_hash']=design_binding(s);s['approved_design_hash']=s['design_hash']
    payload=json.loads(plan(s['obligation_contract']))
    payload['test_stage']={'allowed_new_test_files':[NEW],'protected_existing_test_files':[TEST]}
    s.update({'plan':json.dumps(payload),'approval':'approve','semantic_valid':True,'plan_lineage_required':True,
      'plan_requirements_hash':s['requirements_hash'],'approved_requirements_hash':s['requirements_hash'],
      'plan_requirement_version':s['requirement_version'],'plan_design_hash':s['design_hash']})
    s['tasks']=tasks_from_plan(s['plan'],'',strict=True);s['approved_task_hash']=task_definition_hash(s['tasks'])
    s['semantic_plan_hash']=digest(s['plan'])
    monkeypatch.setattr(audit_logger,'DB_PATH',tmp_path/'audit.db')
    return s


def execute(s):
    s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps({'edits':[{'path':SOURCE,'old_text':'original','new_text':'correct'}]}))))
    with patch('agents.implementation_agent.model',model):tx=implement_task(s['tasks'][0],Path(s['workspace']),s)
    return tx,model


def test_prospective_plan_execution_test_chain_and_authoritative_prompt(approved_state):
    s=approved_state;tx,model=execute(s);execution=tx['execution_receipt']
    assert (Path(s['workspace'])/TEST).read_text()=='original'
    prompt=model.invoke.call_args.args[0]
    for field in ('structured_requirements','clarification_answers','approved_obligation_contract','approved_design','approved_plan','plan_authorization'):
        assert field in prompt
    finish_transaction(tx['transaction_id'],Path(s['workspace']),[SOURCE],passed=True)
    test=(Path(s['workspace'])/NEW);test.write_text('controlled generated fixture')
    validation={'exit_code':0,'cases':{'fixture#boundary':'passed'}};scope='scope'
    log_event(s['workflow_id'],'fixture','TEST_SCOPE_APPROVED',{'scope_hash':scope})
    log_event(s['workflow_id'],'fixture','TEST_FILE_CREATED',{'path':NEW,'sha256':sha(test.read_bytes()),'source_transaction':tx['transaction_id']})
    log_event(s['workflow_id'],'fixture','TEST_STAGE_EXIT_PASSED',{'scope_hash':scope,'test_hash':sha(test.read_bytes()),'validation':validation})
    link=link_tests(s,execution,NEW,sha(test.read_bytes()),scope,validation)
    assert verify_test_link(s,execution,link,tx['transaction_id'],NEW,sha(test.read_bytes()),scope,validation)==digest(s['plan'])
    assert link['authorization_hash']==s['plan_authorization']['authorization_hash']
    with sqlite3.connect(s['plan_authorization_audit_db']) as c:
        events=[x[0] for x in c.execute('select event from audit_events order by id')]
    assert events.index('ENGINEERING_PLAN_AUTHORIZED')<events.index('TRANSACTION_APPLIED')<events.index('ENGINEERING_EXECUTION_BOUND')
    assert events.count('ENGINEERING_TESTS_BOUND')==1


@pytest.mark.parametrize('change',['plan','requirements','design','workspace','receipt','scope_only'])
def test_scope_or_stale_identity_cannot_authorize_generation(approved_state,tmp_path,change):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    if change=='plan':s['plan']+=' '
    if change=='requirements':s['requirement_version']+=1
    if change=='design':s['design_hash']='different'
    if change=='workspace':
        root=tmp_path/'other';shutil.copytree(s['workspace'],root,ignore=shutil.ignore_patterns('other'));s['workspace']=str(root)
    if change=='receipt':s['plan_authorization']['authorization_hash']='tampered'
    if change=='scope_only':s.pop('plan_authorization');s['approved_scope_hash']='limited-scope'
    with patch('agents.implementation_agent.model') as model:
        with pytest.raises((PermissionError,ValueError)):implement_task(s['tasks'][0],Path(s['workspace']),s)
    model.invoke.assert_not_called()


@pytest.mark.parametrize('change',['source','test','build','extra'])
def test_changed_approved_baseline_blocks_before_generation(approved_state,change):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    path=SOURCE if change=='source' else TEST if change=='test' else 'url-shortener/pom.xml' if change=='build' else 'url-shortener/extra.txt'
    (Path(s['workspace'])/path).write_text('changed')
    with patch('agents.implementation_agent.model') as model:
        with pytest.raises(PermissionError):implement_task(s['tasks'][0],Path(s['workspace']),s)
    model.invoke.assert_not_called()


def test_binding_failure_compensates_source_transaction(approved_state):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps({'edits':[{'path':SOURCE,'old_text':'original','new_text':'correct'}]}))))
    with patch('agents.implementation_agent.model',model),patch('governance.plan_lineage.bind_execution',side_effect=PermissionError('receipt failure')):
        with pytest.raises(PermissionError):implement_task(s['tasks'][0],Path(s['workspace']),s)
    assert (Path(s['workspace'])/SOURCE).read_text()=='original'
    assert (Path(s['workspace'])/TEST).read_text()=='original'


def test_unprotected_existing_tests_rejected_by_plan_authorization(approved_state):
    s=approved_state;payload=json.loads(s['plan']);payload['test_stage']['protected_existing_test_files']=[]
    s['plan']=json.dumps(payload);s['semantic_plan_hash']=digest(s['plan'])
    with pytest.raises(PermissionError):authorize(s)


def test_historical_application_cannot_gain_later_approval(approved_state):
    s=approved_state;tx,_=execute(s)
    with sqlite3.connect(s['plan_authorization_audit_db']) as c:
        row=c.execute("select workflow_id,agent,event,details from audit_events where event='ENGINEERING_PLAN_AUTHORIZED'").fetchone()
        c.execute("delete from audit_events where event='ENGINEERING_PLAN_AUTHORIZED'")
    log_event(row[0],row[1],row[2],json.loads(row[3]))
    with pytest.raises(PermissionError,match='Approval must precede'):bind_execution(s,s['tasks'][0],tx)


@pytest.mark.parametrize('fault',['no_scope','no_create','no_exit','failed_validation','not_validated','tampered_execution','original_test_changed','journal_workflow','journal_hash'])
def test_test_linkage_requires_actual_matching_stage_chain(approved_state,fault):
    s=approved_state;tx,_=execute(s);execution=tx['execution_receipt']
    if fault!='not_validated':finish_transaction(tx['transaction_id'],Path(s['workspace']),[SOURCE],passed=True)
    test=Path(s['workspace'])/NEW;test.write_text('fixture assertions');test_hash=sha(test.read_bytes())
    validation={'exit_code':1 if fault=='failed_validation' else 0,'cases':{'fixture#test':'passed'}}
    if fault!='no_scope':log_event(s['workflow_id'],'fixture','TEST_SCOPE_APPROVED',{'scope_hash':'scope'})
    if fault!='no_create':log_event(s['workflow_id'],'fixture','TEST_FILE_CREATED',{'path':NEW,'sha256':test_hash,'source_transaction':tx['transaction_id']})
    if fault!='no_exit':log_event(s['workflow_id'],'fixture','TEST_STAGE_EXIT_PASSED',{'scope_hash':'scope','test_hash':test_hash,'validation':validation})
    if fault=='tampered_execution':execution['execution_hash']='bad'
    if fault=='original_test_changed':(Path(s['workspace'])/TEST).write_text('weakened')
    if fault in {'journal_workflow','journal_hash'}:
        journal_path=Path(s['workspace'])/'.agent-transactions'/(tx['transaction_id']+'.json')
        journal=json.loads(journal_path.read_text())
        if fault=='journal_workflow':journal['workflow_id']='foreign'
        else:journal['files'][0]['expected_current_hash']='tampered'
        journal_path.write_text(json.dumps(journal))
    with pytest.raises(PermissionError):link_tests(s,execution,NEW,test_hash,'scope',validation)


@pytest.mark.parametrize('decision',['approve','reject'])
def test_main_review_mints_receipt_only_after_explicit_approval(approved_state,decision):
    import main
    s=approved_state
    with patch('main.interrupt',return_value=decision):result=main.human_approval(s)
    if decision=='approve':assert result['plan_authorization']['authorization_hash']
    else:assert not result.get('plan_authorization') and not result['approved_task_hash']
    assert (Path(s['workspace'])/SOURCE).read_text()=='original'


def test_targeted_retry_receives_the_same_authoritative_approved_context(approved_state):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    s['previous_implementation_proposal']={'edits':[{'path':SOURCE,'old_text':'original','new_text':'failed'}]}
    s['previous_validation_result']={'passed':False,'exit_code':1,'output':'[ERROR] compilation'}
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps({'edits':[{'path':SOURCE,'old_text':'original','new_text':'correct'}]}))))
    with patch('agents.implementation_agent.model',model):implement_task(s['tasks'][0],Path(s['workspace']),s)
    prompt=model.invoke.call_args.args[0]
    assert 'Authoritative approved requirement' in prompt
    for key in ('clarification_answers','approved_obligation_contract','approved_design','approved_plan'):assert key in prompt


def test_model_cannot_edit_read_only_test_scope(approved_state):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    payload={'edits':[{'path':SOURCE,'old_text':'original','new_text':'correct'},
                      {'path':TEST,'old_text':'original','new_text':'weakened'}]}
    with patch('agents.implementation_agent.model',Mock(invoke=Mock(return_value=ModelResponse(json.dumps(payload))))):
        with pytest.raises(PermissionError):implement_task(s['tasks'][0],Path(s['workspace']),s)
    assert (Path(s['workspace'])/SOURCE).read_text()=='original' and (Path(s['workspace'])/TEST).read_text()=='original'


def test_explicit_foreign_executor_workspace_blocked_before_model_or_write(approved_state,tmp_path):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    other=tmp_path/'foreign';shutil.copytree(s['workspace'],other,ignore=shutil.ignore_patterns('foreign'))
    with patch('agents.implementation_agent.model') as model:
        with pytest.raises(PermissionError):implement_task(s['tasks'][0],other,s)
    model.invoke.assert_not_called();assert (other/SOURCE).read_text()=='original'
