import copy
import json
import sqlite3
from unittest.mock import Mock,patch

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.design import assert_design_current
from llm.provider import ModelResponse
from test_support import requirements_response
from test_design_stage import state,artifact,QUESTION,SOURCE,TEST
from test_semantic_plan import plan
import main


def generator(s,unchanged=False):
    original=artifact(s['obligation_contract'])
    original['compatibility_risks'][0]['description']='Expiration decision is not approved yet.'
    revised=copy.deepcopy(original)
    revised['compatibility_risks'][0]={'id':'risk-1','description':'Existing links must remain accessible.',
                                       'mitigation':'Preserve missing-expiration behavior; A1 custom UTC expiration is already approved.'}
    calls=[]
    def invoke(prompt,*,stage):
        if stage=='requirements':return requirements_response('Custom expiration',[QUESTION])
        if stage=='planning':return ModelResponse(plan(s['obligation_contract']))
        assert stage=='design';calls.append(prompt)
        return ModelResponse(json.dumps(original if unchanged or len(calls)==1 else revised))
    return Mock(invoke=Mock(side_effect=invoke)),calls,original,revised


def start(app,s,config):
    app.invoke({'workflow_id':config['configurable']['thread_id'],'workspace':s['workspace'],
                'requirement':'custom expiration','design_required':True,'obligation_contract':s['obligation_contract']},config=config)
    app.invoke(Command(resume={'decision':'approve','answers':{'A1':'Custom expiration timestamp'}}),config=config)
    return app.invoke(Command(resume='approve'),config=config)


@pytest.mark.parametrize('unchanged',[False,True])
def test_versioned_design_correction_preserves_contract_and_blocks_old_approval(tmp_path,unchanged):
    s=state(tmp_path);provider,calls,original,revised=generator(s,unchanged)
    config={'configurable':{'thread_id':'correction'}}
    with patch('main.model',provider),patch('main.analyze_codebase',return_value='Java'),patch('main.implement_task') as implementation,patch.object(
        audit_logger,'DB_PATH',tmp_path/'audit.db'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver);before=start(app,s,config)
        old_hash=before['design_hash']
        result=app.invoke(Command(resume={'decision':'request_changes','feedback':'A1 is approved; replace the stale risk.'}),config=config)
        for key in ('requirements_hash','requirement_version','approved_contract_hash','clarification_answers'):
            assert result[key]==before[key]
        assert 'A1 is approved; replace the stale risk.' in calls[1]
        assert 'Expiration decision is not approved yet.' in calls[1]
        assert not result['approved_design_hash'] and not result.get('plan') and not result.get('approved_task_hash')
        assert result['semantic_attempts']==0 and result['semantic_errors']==[]
        assert result['design_versions'][0]['artifact']==original
        implementation.assert_not_called()
        if unchanged:
            assert result['status']=='STOPPED_DESIGN_INVALID'
            assert len(calls)==4 and not app.get_state(config).next
        else:
            assert app.get_state(config).next==('design_review',)
            assert result['design_version']==2 and result['design_hash']!=old_hash
            assert result['design_artifact']==revised and len(result['design_artifact']['coverage'])==8
            assert result['design_versions'][1]['artifact']==revised
            assert_design_current(result)
            forged=copy.deepcopy(result);forged['approved_design_hash']=old_hash
            with pytest.raises(PermissionError):assert_design_current(forged,approved=True)
            accepted=app.invoke(Command(resume='approve'),config=config)
            assert app.get_state(config).next==('approval',)
            assert accepted['approved_design_hash']==accepted['plan_design_hash']==result['design_hash']
        assert all((tmp_path/name).read_text()=='original' for name in (SOURCE,TEST))
        with sqlite3.connect(tmp_path/'audit.db') as conn:
            rows=conn.execute("select details from audit_events where event='DESIGN_CHANGES_REQUESTED'").fetchall()
        assert len(rows)==1 and json.loads(rows[0][0])['artifact']==original
        assert json.loads(rows[0][0])['design_hash']==old_hash


@pytest.mark.parametrize('response',['request_changes',{'decision':'request_changes','feedback':''},
    {'decision':'request_changes','feedback':'   '},{'decision':'request_changes','feedback':[]},
    {'decision':'request_changes','feedback':'fix','requirement':'different'},'unknown'])
def test_invalid_review_feedback_reinterrupts_without_regeneration(tmp_path,response):
    s=state(tmp_path);provider,calls,_,_=generator(s)
    config={'configurable':{'thread_id':'invalid-review'}}
    with patch('main.model',provider),patch('main.analyze_codebase',return_value='Java'),patch('main.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver);before=start(app,s,config)
        result=app.invoke(Command(resume=response),config=config)
        assert result['__interrupt__'][0].value['type']=='DESIGN_APPROVAL'
        assert result['__interrupt__'][0].value['errors']
        assert result['design_hash']==before['design_hash'] and len(calls)==1
        assert not result['approved_design_hash']


def test_cli_correction_feedback_and_rejection(tmp_path,monkeypatch):
    s=state(tmp_path);provider,calls,_,_=generator(s)
    monkeypatch.chdir(tmp_path)
    inputs=iter(['custom expiration','1','1','1','3','Replace stale unapproved decision risk.','2'])
    with patch('builtins.input',side_effect=lambda _:next(inputs)),patch('main.initialize_workspace',return_value=tmp_path),patch(
        'main.model',provider),patch('main.analyze_codebase',return_value='Java'),patch.object(audit_logger,'DB_PATH',tmp_path/'audit.db'):
        result=main.main(obligation_contract=s['obligation_contract'],design_required=True)
    assert result['status']=='STOPPED_DESIGN_REJECTED' and result['design_version']==2
    assert 'Replace stale unapproved decision risk.' in calls[1]
