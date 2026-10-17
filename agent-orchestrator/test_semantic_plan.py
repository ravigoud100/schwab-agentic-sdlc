import copy
import json
import sqlite3
from unittest.mock import Mock, patch

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.requirements import assert_plan_current
from governance.semantic_plan import custom_expiration_contract, validate_coverage, SemanticPlanError
from llm.provider import ModelResponse
from test_support import requirements_response
import main

PATH = 'url-shortener/src/main/java/url_shortener/ShortUrl.java'
TEST = 'url-shortener/src/test/java/url_shortener/ShortUrlTest.java'
QUESTION = {'id':'A1','question':'Choose expiration mode','options':['Custom expiration timestamp'], 'default_assumption':None}


def plan(contract, covered=True):
    payload = {'tasks':[{'task_id':'expiration','description':'Accept caller-provided future UTC expiration; preserve missing expiration and test boundaries',
               'dependencies':[], 'execution_type':'implementation','allowed_files':[PATH,TEST]}]}
    if covered:
        payload['coverage'] = [{'obligation_id':o['id'],'kind':o['kind'],'spec':o['spec'],
                                'task_id':'expiration','files':[TEST if o['kind']=='test' else PATH]}
                               for o in contract['obligations']]
    return json.dumps(payload)


def setup_files(tmp_path):
    for name in [PATH,TEST]:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original')


@pytest.mark.parametrize('fault',['missing','wrong_spec','duplicate','unknown','unmapped_task','outside_scope','test_in_source'])
def test_rejects_unproven_coverage(fault):
    contract=custom_expiration_contract()
    payload=json.loads(plan(contract))
    if fault=='missing':payload['coverage'].pop()
    if fault=='wrong_spec':payload['coverage'][0]['spec']['source']='fixed_ttl'
    if fault=='duplicate':payload['coverage'].append(payload['coverage'][0])
    if fault=='unknown':payload['coverage'][0]['obligation_id']='other'
    if fault=='unmapped_task':payload['coverage'][0]['task_id']='other'
    if fault=='outside_scope':payload['coverage'][0]['files']=['outside.java']
    if fault=='test_in_source':payload['coverage'][-1]['files']=[PATH]
    from governance.task_orchestrator import tasks_from_plan
    with pytest.raises(SemanticPlanError):
        validate_coverage(json.dumps(payload),tasks_from_plan(json.dumps(payload),'',strict=True),contract)


@pytest.mark.parametrize('exhaust',[False,True])
def test_graph_retries_automatically_preserves_decision_and_stops_safely(tmp_path,exhaust):
    setup_files(tmp_path)
    contract=custom_expiration_contract();prompts=[]
    def invoke(prompt,*,stage):
        if stage=='requirements':return requirements_response('Custom expiration',[QUESTION])
        prompts.append(prompt)
        return ModelResponse(plan(contract,covered=not exhaust and len(prompts)>1))
    config={'configurable':{'thread_id':'semantic-thread'}}
    with patch('main.model',Mock(invoke=Mock(side_effect=invoke))),patch('main.analyze_codebase',return_value='Java'),patch(
        'main.implement_task') as implementation,patch.object(audit_logger,'DB_PATH',tmp_path/'audit.db'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        app.invoke({'workflow_id':'semantic-thread','workspace':str(tmp_path),'requirement':'Configure expiration',
                    'obligation_contract':contract,'approval':''},config=config)
        paused=app.invoke(Command(resume={'decision':'approve','answers':{'A1':'Custom expiration timestamp'}}),config=config)
        assert paused['__interrupt__'][0].value['type']=='REQUIREMENT_CONTRACT_APPROVAL'
        before=app.get_state(config).values
        result=app.invoke(Command(resume='approve'),config=config)
        assert result['requirements_hash']==before['requirements_hash']
        assert result['requirement_version']==before['requirement_version']
        assert app.get_state(config).config['configurable']['thread_id']=='semantic-thread'
        implementation.assert_not_called()
        assert all((tmp_path/name).read_text()=='original' for name in [PATH,TEST])
        assert 'Plan requires a structured coverage array' in prompts[1]
        if exhaust:
            assert result['status']=='STOPPED_SEMANTIC_PLAN_INVALID'
            assert len(prompts)==3 and not app.get_state(config).next
            assert not result.get('plan') and not result.get('approved_task_hash')
        else:
            assert len(prompts)==2 and result['semantic_valid']
            assert app.get_state(config).next==('approval',)
            assert_plan_current(result)
            tampered=copy.deepcopy(result);tampered['plan']=plan(contract,False)
            with pytest.raises(PermissionError):assert_plan_current(tampered)
        with sqlite3.connect(tmp_path/'audit.db') as conn:
            failures=conn.execute("select details from audit_events where event='SEMANTIC_PLAN_REJECTED'").fetchall()
        assert len(failures)==(3 if exhaust else 1)
        assert all(json.loads(row[0])['requirements_hash']==before['requirements_hash'] for row in failures)


def test_contract_rejection_stops_before_planning(tmp_path):
    setup_files(tmp_path)
    provider=Mock(invoke=Mock(return_value=requirements_response('Custom expiration',[QUESTION])))
    with patch('main.model',provider),patch('main.analyze_codebase',return_value='Java'),patch('main.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver);config={'configurable':{'thread_id':'reject'}}
        app.invoke({'workflow_id':'reject','workspace':str(tmp_path),'requirement':'Custom expiration',
                    'obligation_contract':custom_expiration_contract()},config=config)
        app.invoke(Command(resume={'decision':'approve','answers':{'A1':'Custom expiration timestamp'}}),config=config)
        result=app.invoke(Command(resume='reject'),config=config)
        assert result['status']=='STOPPED_CONTRACT_REJECTED'
        assert provider.invoke.call_count==1


@pytest.mark.parametrize('change',['contract','version','answers','missing_contract'])
def test_approval_binding_cannot_be_reused(change):
    from governance.semantic_plan import binding, assert_contract_approved
    state={'obligation_contract':custom_expiration_contract(),'requirements_hash':'requirements',
           'requirement_version':1,'clarification_answers':{'A1':'Custom expiration timestamp'}}
    state['approved_contract_hash']=binding(state)
    if change=='contract':state['obligation_contract']['obligations'].pop()
    if change=='version':state['requirement_version']=2
    if change=='answers':state['clarification_answers']['A1']='Fixed TTL'
    if change=='missing_contract':
        state['obligation_contract']={};state['semantic_required']=True
        with pytest.raises((PermissionError,ValueError)):assert_plan_current(state)
        return
    with pytest.raises((PermissionError,SemanticPlanError)):assert_contract_approved(state)


def test_changed_requirements_keep_semantic_requirement_but_clear_old_contract(tmp_path):
    from governance.semantic_plan import binding
    state={'requirement':'old requirement','workflow_id':'change','requirement_version':1,
           'obligation_contract':custom_expiration_contract(),'semantic_required':True,
           'change_request':'new requirement'}
    with patch('main.log_event'):
        changed=main.requirement_changes_node(state)
    combined={**state,**changed}
    assert combined['semantic_required'] and combined['obligation_contract']=={}
    result=main.contract_review_node(combined)
    assert result['status']=='STOPPED_CONTRACT_REQUIRED'


@pytest.mark.parametrize('invalid',['wrong_answer','duplicate','empty'])
def test_invalid_contract_stops_before_model_call(invalid):
    contract=custom_expiration_contract()
    if invalid=='wrong_answer':contract['selected_answer']='Fixed TTL'
    if invalid=='duplicate':contract['obligations'].append(copy.deepcopy(contract['obligations'][0]))
    if invalid=='empty':contract['obligations']=[]
    state={'workflow_id':'invalid','obligation_contract':contract,
           'clarification_answers':{'A1':'Custom expiration timestamp'}}
    with patch('main.assert_resolved'),patch('main.log_event'),patch('main.model') as model:
        result=main.contract_review_node(state)
    assert result['status']=='STOPPED_CONTRACT_INVALID'
    model.invoke.assert_not_called()


def test_planner_requires_contract_approval_before_generation():
    state={'obligation_contract':custom_expiration_contract(),'requirements_hash':'req',
           'requirement_version':1,'clarification_answers':{'A1':'Custom expiration timestamp'}}
    with patch('main.assert_resolved'),patch('main.model') as model:
        with pytest.raises(PermissionError):main.planning_agent(state)
    model.invoke.assert_not_called()
