import copy
import json
from unittest.mock import Mock, patch
import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from llm.provider import ModelResponse
from governance import audit_logger
from governance.requirements import RequirementsError,parse_requirements,assert_plan_current
from test_support import requirements_payload,requirements_response
import main

QUESTION={'id':'A1','question':'How should expiration be configured?',
          'options':['Fixed 24-hour TTL','Caller-provided timestamp'],
          'default_assumption':'Caller-provided timestamp'}
PATH=main.IMPLEMENTATION_RELATIVE_PATH


def plan_for(answer):
    return {'tasks':[{'task_id':'configure-expiration','description':'Implement '+answer,
                     'dependencies':[],'execution_type':'implementation','allowed_files':[PATH]}]}


@pytest.mark.parametrize('answer',QUESTION['options'])
def test_clarification_is_persisted_in_same_thread_and_changes_planning(tmp_path,answer):
    def invoke(prompt,*,stage):
        if stage=='requirements': return requirements_response('Configure URL expiration',[QUESTION])
        assert stage=='planning'
        assert json.dumps({'A1':answer}) in prompt
        return ModelResponse(json.dumps(plan_for(answer)))
    provider=Mock(invoke=Mock(side_effect=invoke))
    codebase=Mock(return_value='Existing Java code')
    config={'configurable':{'thread_id':'clarification-thread'}}
    with patch('main.model',provider),patch('main.analyze_codebase',codebase),patch('main.implement_task') as implementation,patch.object(
        audit_logger,'DB_PATH',tmp_path/'audit.db'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        paused=app.invoke({'workflow_id':'clarification-thread','workspace':str(tmp_path),
                           'requirement':'Add configurable expiration','approval':''},config=config)
        assert paused['__interrupt__'][0].value['type']=='REQUIREMENT_CLARIFICATION'
        assert paused['clarification_answers']=={}
        codebase.assert_not_called()
        assert provider.invoke.call_count==1
        result=app.invoke(Command(resume={'decision':'approve','answers':{'A1':answer}}),config=config)
        assert result['__interrupt__'][0].value['allowed_decisions']==['approve','reject','request_changes']
        assert result['clarification_answers']=={'A1':answer}
        assert answer in result['plan']
        assert answer in codebase.call_args.args[0]
        assert result['plan_requirements_hash']==result['requirements_hash']
        assert app.get_state(config).config['configurable']['thread_id']=='clarification-thread'
        assert app.get_state(config).values['clarification_answers']=={'A1':answer}
        implementation.assert_not_called()
        with sqlite3.connect(tmp_path/'audit.db') as conn:
            decisions=conn.execute("SELECT details FROM audit_events WHERE workflow_id=? AND event='CLARIFICATION_APPROVED'",('clarification-thread',)).fetchall()
        assert len(decisions)==1
        assert json.loads(decisions[0][0])['answers']=={'A1':answer}
        result=app.invoke(Command(resume='reject'),config=config)
        assert result['status']=='STOPPED'
        assert not app.get_state(config).next


@pytest.mark.parametrize('response',[
    'approve',{'decision':'approve','answers':{}},
    {'decision':'approve','answers':{'A1':''}},
    {'decision':'approve','answers':{'A1':'Invented option'}},
    {'decision':'approve','answers':{'A1':'Fixed 24-hour TTL','A2':'extra'}},
    {'answers':{'A1':'Fixed 24-hour TTL'}},
])
def test_invalid_or_incomplete_answers_reinterrupt_without_bypass(tmp_path,response):
    provider=Mock(invoke=Mock(return_value=requirements_response('Expiration',[QUESTION])))
    config={'configurable':{'thread_id':'invalid-answers'}}
    with patch('main.model',provider),patch('main.analyze_codebase') as codebase,patch('main.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        app.invoke({'workflow_id':'invalid-answers','workspace':str(tmp_path),'requirement':'Expiration'},config=config)
        result=app.invoke(Command(resume=response),config=config)
        assert result['__interrupt__'][0].value['type']=='REQUIREMENT_CLARIFICATION'
        assert result['__interrupt__'][0].value['errors']
        assert result['clarification_complete'] is False
        assert result['clarification_answers']=={}
        assert result['clarification_round']==1
        codebase.assert_not_called()
        assert provider.invoke.call_count==1


def test_invalid_requirements_stop_before_analysis_or_planning(tmp_path):
    with patch('main.model',Mock(invoke=Mock(return_value=ModelResponse('prose instead of JSON')))),patch(
        'main.analyze_codebase') as codebase,patch('main.log_event'):
        result=main.workflow.compile().invoke({'workflow_id':'bad-json','workspace':str(tmp_path),'requirement':'Expiration'})
    assert result['status']=='STOPPED_INVALID_REQUIREMENTS'
    codebase.assert_not_called()


@pytest.mark.parametrize('mutation', ['duplicate_id','bad_options','bad_default','missing_field','bad_classification'])
def test_schema_rejects_invalid_structures(mutation):
    payload=requirements_payload('Expiration',[copy.deepcopy(QUESTION)])
    if mutation=='duplicate_id': payload['ambiguities'].append(copy.deepcopy(QUESTION))
    if mutation=='bad_options': payload['ambiguities'][0]['options']='TTL'
    if mutation=='bad_default': payload['ambiguities'][0]['default_assumption']='Unlisted'
    if mutation=='missing_field': del payload['acceptance_criteria']
    if mutation=='bad_classification': payload['classification']='whatever'
    with pytest.raises(RequirementsError): parse_requirements(json.dumps(payload))


def test_request_changes_invalidates_old_plan_and_approval_then_reanalyzes(tmp_path):
    from llm.provider import ModelResponse
    first=requirements_response('Use caller-provided expiration')
    second=requirements_response('Use fixed 24-hour TTL')
    provider=Mock(invoke=Mock(side_effect=[first,ModelResponse(json.dumps(plan_for('Caller-provided timestamp'))),
                                         second,ModelResponse(json.dumps(plan_for('Fixed 24-hour TTL')))]))
    config={'configurable':{'thread_id':'changes'}}
    with patch('main.model',provider),patch('main.analyze_codebase',return_value='Source'),patch('main.implement_task') as execute,patch.object(
        audit_logger,'DB_PATH',tmp_path/'audit.db'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        initial=app.invoke({'workflow_id':'changes','workspace':str(tmp_path),'requirement':'Use timestamps'},config=config)
        original_hash=initial['requirements_hash']
        changed=app.invoke(Command(resume={'decision':'request_changes','requirement':'Use fixed 24-hour TTL'}),config=config)
        assert changed['requirement_version']==2
        assert changed['requirements_hash']!=original_hash
        assert changed['approval']==''
        assert changed['approved_task_hash']==changed['approved_requirements_hash']==''
        assert changed['clarification_answers']=={}
        assert 'Fixed 24-hour TTL' in changed['plan']
        assert changed['plan_version']==2
        assert changed['plan_requirements_hash']==changed['requirements_hash']
        assert changed['requirement_history'][0]['plan']==initial['plan']
        execute.assert_not_called()
        assert '__interrupt__' in changed
        with sqlite3.connect(tmp_path/'audit.db') as conn:
            events=[row[0] for row in conn.execute('SELECT event FROM audit_events')]
        assert 'DOWNSTREAM_PLANS_INVALIDATED' in events
        assert 'REQUIREMENTS_CHANGED' in events


@pytest.mark.parametrize('field,value', [('requirement','Changed raw requirement'),('requirement_version',2)])
def test_changed_requirements_cannot_use_stale_approved_plan(tmp_path,field,value):
    from llm.provider import ModelResponse
    with patch('main.model',Mock(invoke=Mock(side_effect=[requirements_response(),ModelResponse(json.dumps(plan_for('Fixed 24-hour TTL')))]))),patch('main.log_event'):
        state={'workflow_id':'stale','workspace':str(tmp_path),'requirement':'Initial requirement'}
        state.update(main.requirements_agent(state))
        state.update(main.planning_agent({**state,'codebase_analysis':'Source'}))
        with patch('main.interrupt',return_value='approve'):
            state.update(main.human_approval(state))
    state[field]=value
    with pytest.raises(PermissionError): assert_plan_current(state,approved=True)


def test_incomplete_answer_can_be_corrected_without_repeated_audit_decisions(tmp_path):
    provider=Mock(invoke=Mock(side_effect=[requirements_response('Expiration',[QUESTION]),
                                         ModelResponse(json.dumps(plan_for('Fixed 24-hour TTL')))]))
    config={'configurable':{'thread_id':'corrected-answer'}}
    with patch('main.model',provider),patch('main.analyze_codebase',return_value='Code'),patch.object(
        audit_logger,'DB_PATH',tmp_path/'audit.db'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        app.invoke({'workflow_id':'corrected-answer','workspace':str(tmp_path),'requirement':'Expiration'},config=config)
        invalid=app.invoke(Command(resume={'decision':'approve','answers':{}}),config=config)
        assert invalid['status']=='CLARIFICATION_REQUIRED'
        corrected=app.invoke(Command(resume={'decision':'approve','answers':{'A1':'Fixed 24-hour TTL'}}),config=config)
        assert corrected['clarification_answers']=={'A1':'Fixed 24-hour TTL'}
        assert len(corrected['clarification_history'])==1
        assert '__interrupt__' in corrected
    with sqlite3.connect(tmp_path/'audit.db') as conn:
        counts=dict(conn.execute('SELECT event,COUNT(*) FROM audit_events GROUP BY event'))
    assert counts['CLARIFICATION_INVALID']==counts['CLARIFICATION_APPROVED']==1
    assert provider.invoke.call_count==2


def test_request_changes_without_text_requests_a_revision_before_any_replanning(tmp_path):
    provider=Mock(invoke=Mock(side_effect=[requirements_response(),ModelResponse(json.dumps(plan_for('Fixed 24-hour TTL')))]))
    config={'configurable':{'thread_id':'revision-needed'}}
    with patch('main.model',provider),patch('main.analyze_codebase',return_value='Code'),patch('main.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        app.invoke({'workflow_id':'revision-needed','workspace':str(tmp_path),'requirement':'Expiration'},config=config)
        result=app.invoke(Command(resume='request_changes'),config=config)
        assert result['__interrupt__'][0].value['type']=='REQUIREMENT_CHANGES'
        assert result['approved_task_hash']==''
        assert result['task_plan_valid'] is False
        assert provider.invoke.call_count==2
        invalid=app.invoke(Command(resume={'decision':'submit','requirement':''}),config=config)
        assert invalid['__interrupt__'][0].value['errors']
        assert provider.invoke.call_count==2


def test_free_text_clarification_requires_explicit_nonempty_approved_answer(tmp_path):
    question={'id':'TTL','question':'Which TTL should be used?','options':[],'default_assumption':None}
    with patch('main.model',Mock(invoke=Mock(return_value=requirements_response('Expiration',[question])))),patch('main.log_event'):
        state={'workflow_id':'free-text','requirement':'Expiration'}
        state.update(main.requirements_agent(state))
        with patch('main.interrupt',return_value={'decision':'approve','answers':{'TTL':'24 hours from creation'}}):
            result=main.clarification_node(state)
    assert result['clarification_complete'] is True
    assert result['clarification_answers']['TTL']=='24 hours from creation'


def test_cli_handles_clarification_before_plan_review(tmp_path,monkeypatch,capsys):
    monkeypatch.chdir(tmp_path)
    inputs=iter(['Configure expiration','1','1','2'])
    provider=Mock(invoke=Mock(side_effect=[requirements_response('Expiration',[QUESTION]),
                                         ModelResponse(json.dumps(plan_for('Fixed 24-hour TTL')))]))
    with patch('builtins.input',side_effect=lambda prompt:next(inputs)),patch('main.initialize_workspace',return_value=tmp_path),patch(
        'main.model',provider),patch('main.analyze_codebase',return_value='Code'),patch.object(
        audit_logger,'DB_PATH',tmp_path/'audit.db'),patch('main.implement_task') as implementation:
        result=main.main()
    assert result['clarification_answers']=={'A1':'Fixed 24-hour TTL'}
    assert result['status']=='STOPPED'
    assert 'REQUIREMENT CLARIFICATION' in capsys.readouterr().out
    implementation.assert_not_called()
    assert (tmp_path/'workflow.db').exists()


def test_cli_request_changes_reanalyzes_and_requires_new_plan_approval(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    inputs=iter(['Use timestamps','3','Use fixed 24-hour TTL','2'])
    provider=Mock(invoke=Mock(side_effect=[requirements_response('Use timestamps'),
        ModelResponse(json.dumps(plan_for('Caller-provided timestamp'))),requirements_response('Use fixed 24-hour TTL'),
        ModelResponse(json.dumps(plan_for('Fixed 24-hour TTL')))]))
    with patch('builtins.input',side_effect=lambda prompt:next(inputs)),patch('main.initialize_workspace',return_value=tmp_path),patch(
        'main.model',provider),patch('main.analyze_codebase',return_value='Code'),patch.object(
        audit_logger,'DB_PATH',tmp_path/'audit.db'),patch('main.implement_task') as implementation:
        result=main.main()
    assert result['requirement_version']==2
    assert result['status']=='STOPPED'
    assert result['requirement']=='Use fixed 24-hour TTL'
    assert result['approval']=='reject'
    implementation.assert_not_called()


def test_new_requirement_version_must_resolve_its_own_ambiguities(tmp_path):
    second_question={'id':'A1','question':'Should the new policy apply to existing links?',
                     'options':['Yes','No'],'default_assumption':'No'}
    provider=Mock(invoke=Mock(side_effect=[requirements_response('Expiration',[QUESTION]),
        ModelResponse(json.dumps(plan_for('Fixed 24-hour TTL'))),requirements_response('Changed expiration',[second_question])]))
    config={'configurable':{'thread_id':'new-ambiguity'}}
    with patch('main.model',provider),patch('main.analyze_codebase',return_value='Code') as codebase,patch('main.log_event'),SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        app.invoke({'workflow_id':'new-ambiguity','workspace':str(tmp_path),'requirement':'Expiration'},config=config)
        app.invoke(Command(resume={'decision':'approve','answers':{'A1':'Fixed 24-hour TTL'}}),config=config)
        changed=app.invoke(Command(resume={'decision':'request_changes','requirement':'Change existing-link expiration policy'}),config=config)
        assert changed['requirement_version']==2
        assert changed['clarification_answers']=={}
        assert changed['__interrupt__'][0].value['questions']==[second_question]
        assert changed['plan']==''
        assert changed['approved_task_hash']==''
        assert codebase.call_count==1
