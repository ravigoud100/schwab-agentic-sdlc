import copy
import json
import sqlite3
from unittest.mock import Mock,patch

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.design import SCHEMA,validate_design,design_binding,assert_design_current,DesignError
from governance.semantic_plan import custom_expiration_contract,binding
from llm.provider import ModelResponse
from test_support import requirements_response
import main

SOURCE='url-shortener/src/main/java/url_shortener/ShortUrl.java'
TEST='url-shortener/src/test/java/url_shortener/ShortUrlTest.java'
QUESTION={'id':'A1','question':'Mode?','options':['Custom expiration timestamp'],'default_assumption':None}


def artifact(contract):
    value=copy.deepcopy(SCHEMA)
    value['components']=[{'id':'service','path':SOURCE,'responsibility':'Accept and validate UTC expiration'},
                         {'id':'tests','path':TEST,'responsibility':'Test expiration boundaries'}]
    value['interactions']=[{'from':'tests','to':'service','operation':'Exercise expiration contract'}]
    value['data_model_changes']=[]
    value['architectural_decisions']=[{'id':'utc','decision':'Use caller-provided future UTC timestamps',
                                     'rationale':'Implements approved clarification'}]
    value['coverage']=[{'obligation_id':o['id'],'kind':o['kind'],'spec':o['spec'],
                        'component_ids':['tests' if o['kind']=='test' else 'service'],
                        'decision_ids':['utc']} for o in contract['obligations']]
    return value


def state(tmp_path):
    (tmp_path/'.git').mkdir(exist_ok=True)
    for name in (SOURCE,TEST):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original')
    value={'workspace':str(tmp_path),'workflow_id':'design','requirement':'custom expiration',
           'requirement_version':1,'requirements_hash':'req','codebase_analysis':'Existing Java',
           'clarification_answers':{'A1':'Custom expiration timestamp'},
           'obligation_contract':custom_expiration_contract(),'design_required':True}
    value['approved_contract_hash']=binding(value)
    return value


@pytest.mark.parametrize('fault',['missing_section','empty_risks','unknown_component','unknown_decision','missing_coverage',
                                  'wrong_spec','test_in_source','path_escape','duplicate_id','required_input','wrong_utc','missing_validation'])
def test_design_exit_gate_rejects_incomplete_or_inconsistent_artifacts(tmp_path,fault):
    s=state(tmp_path);a=artifact(s['obligation_contract'])
    if fault=='missing_section':a.pop('api_contracts')
    if fault=='empty_risks':a['compatibility_risks']=[]
    if fault=='unknown_component':a['interactions'][0]['to']='unknown'
    if fault=='unknown_decision':a['coverage'][0]['decision_ids']=['unknown']
    if fault=='missing_coverage':a['coverage'].pop()
    if fault=='wrong_spec':a['coverage'][0]['spec']={'operation':'fixed_ttl'}
    if fault=='test_in_source':a['coverage'][-1]['component_ids']=['service']
    if fault=='path_escape':a['components'][0]['path']='../Outside.java'
    if fault=='duplicate_id':a['components'].append(a['components'][0])
    if fault=='required_input':a['api_contracts'][0]['request_fields'][0]['required']=True
    if fault=='wrong_utc':a['api_contracts'][0]['request_fields'][0]['type']='local_time'
    if fault=='missing_validation':a['api_contracts'][0]['validation_rules']=[]
    with patch('governance.design.assert_resolved'):
        with pytest.raises((ValueError,PermissionError,OSError)):validate_design(a,s)


@pytest.mark.parametrize('exhaust',[False,True])
def test_design_graph_gate_retry_approval_and_source_preservation(tmp_path,exhaust):
    s=state(tmp_path);calls=[]
    def invoke(prompt,*,stage):
        if stage=='requirements':return requirements_response('Custom expiration',[QUESTION])
        assert stage=='design'
        calls.append(prompt)
        return ModelResponse('{}' if exhaust or len(calls)==1 else json.dumps(artifact(s['obligation_contract'])))
    config={'configurable':{'thread_id':'design'}}
    with patch('main.model',Mock(invoke=Mock(side_effect=invoke))),patch('main.analyze_codebase',return_value='Existing Java'),patch(
        'main.implement_task') as implement,patch.object(audit_logger,'DB_PATH',tmp_path/'audit.db'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        app.invoke({'workflow_id':'design','workspace':str(tmp_path),'requirement':'custom expiration',
                    'obligation_contract':s['obligation_contract'],'design_required':True},config=config)
        app.invoke(Command(resume={'decision':'approve','answers':{'A1':'Custom expiration timestamp'}}),config=config)
        result=app.invoke(Command(resume='approve'),config=config)
        implement.assert_not_called()
        assert all((tmp_path/name).read_text()=='original' for name in (SOURCE,TEST))
        if exhaust:
            assert len(calls)==3 and result['status']=='STOPPED_DESIGN_INVALID'
            assert not app.get_state(config).next
        else:
            assert len(calls)==2 and result['__interrupt__'][0].value['type']=='DESIGN_APPROVAL'
            assert app.get_state(config).next==('design_review',)
            assert_design_current(result)
            with pytest.raises(PermissionError):assert_design_current(result,approved=True)
            rejected=app.invoke(Command(resume='reject'),config=config)
            assert rejected['status']=='STOPPED_DESIGN_REJECTED'
            assert not app.get_state(config).next
        with sqlite3.connect(tmp_path/'audit.db') as conn:
            events=conn.execute("select details from audit_events where event='STAGE_GATE_REJECTED'").fetchall()
        assert len(events)==(3 if exhaust else 1)
        assert 'Design must contain exactly' in calls[1]


def test_design_approval_binds_artifact_and_requirement_identity(tmp_path):
    s=state(tmp_path);s['design_artifact']=artifact(s['obligation_contract']);s['design_valid']=True
    s['design_hash']=design_binding(s);s['approved_design_hash']=s['design_hash']
    with patch('governance.design.assert_resolved'):
        assert_design_current(s,approved=True)
        stale=copy.deepcopy(s);stale['design_artifact']['architectural_decisions'][0]['decision']='different'
        with pytest.raises(PermissionError):assert_design_current(stale,approved=True)
        stale=copy.deepcopy(s);stale['requirement_version']=2
        with pytest.raises(PermissionError):assert_design_current(stale,approved=True)


@pytest.mark.parametrize('fault',['no_workspace','uninitialized','no_analysis','unapproved_contract'])
def test_design_entry_gate_blocks_generation(tmp_path,fault):
    s=state(tmp_path)
    if fault=='no_workspace':s['workspace']=str(tmp_path/'missing')
    if fault=='uninitialized':(tmp_path/'.git').rmdir()
    if fault=='no_analysis':s['codebase_analysis']=''
    if fault=='unapproved_contract':s['approved_contract_hash']=''
    with patch('governance.design.assert_resolved'),patch('main.model') as model,patch('main.log_event'):
        result=main.design_node(s)
    assert result['status']=='STOPPED_DESIGN_ENTRY_GATE'
    model.invoke.assert_not_called()


def test_planning_blocked_before_design_approval(tmp_path):
    s=state(tmp_path)
    s['design_artifact']=artifact(s['obligation_contract']);s['design_valid']=True;s['design_hash']=design_binding(s)
    with patch('main.assert_resolved'),patch('governance.design.assert_resolved'),patch('main.model') as model:
        with pytest.raises(PermissionError):main.planning_agent(s)
    model.invoke.assert_not_called()


def test_approved_design_is_bound_to_plan_and_enters_planning_prompt(tmp_path):
    from test_semantic_plan import plan
    s=state(tmp_path);planning_prompts=[]
    def invoke(prompt,*,stage):
        if stage=='requirements':return requirements_response('Custom expiration',[QUESTION])
        if stage=='design':return ModelResponse(json.dumps(artifact(s['obligation_contract'])))
        assert stage=='planning'
        planning_prompts.append(prompt)
        return ModelResponse(plan(s['obligation_contract']))
    config={'configurable':{'thread_id':'design-approved'}}
    with patch('main.model',Mock(invoke=Mock(side_effect=invoke))),patch('main.analyze_codebase',return_value='Java'),patch(
        'main.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        app.invoke({'workflow_id':'design-approved','workspace':str(tmp_path),'requirement':'custom expiration',
                    'design_required':True,'obligation_contract':s['obligation_contract']},config=config)
        app.invoke(Command(resume={'decision':'approve','answers':{'A1':'Custom expiration timestamp'}}),config=config)
        reviewed=app.invoke(Command(resume='approve'),config=config)
        assert reviewed['__interrupt__'][0].value['type']=='DESIGN_APPROVAL'
        assert not planning_prompts
        result=app.invoke(Command(resume='approve'),config=config)
        assert app.get_state(config).next==('approval',)
        assert result['plan_design_hash']==result['approved_design_hash']==result['design_hash']
        assert 'Use caller-provided future UTC timestamps' in planning_prompts[0]
        from governance.requirements import assert_plan_current
        assert_plan_current(result)
        result['plan_design_hash']='old-design'
        with pytest.raises(PermissionError):assert_plan_current(result)


def test_requirement_changes_invalidate_design(tmp_path):
    s=state(tmp_path)
    s.update({'change_request':'different requirement','design_valid':True,'design_hash':'old',
              'approved_design_hash':'old','plan_design_hash':'old'})
    with patch('main.log_event'):
        updated=main.requirement_changes_node(s)
    assert not updated['design_valid'] and not updated['approved_design_hash']
    assert not updated['design_artifact'] and not updated['plan_design_hash']
