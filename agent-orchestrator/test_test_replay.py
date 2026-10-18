"""Wrong-stage obligations stay rejected; exact replay cannot reset source budget."""
import copy,json
from pathlib import Path
from unittest.mock import Mock
import pytest
from agents.test_generation_agent import generate_spec,validate_spec,CASES
from governance.semantic_plan import custom_expiration_contract
from governance.test_replay import replay
from governance.plan_lineage import authorize
from llm.provider import ModelResponse
from test_plan_lineage import approved_state,SOURCE


def test_filtered_test_payload_is_explicit_and_implementation_ids_stay_rejected():
    contract=custom_expiration_contract()
    wrong={'cases':[{'obligation_id':o['id'],'spec':o['spec']} for o in contract['obligations'] if o['kind']=='implementation']}
    context={'structured_requirements':{},'clarification_answers':{},'obligation_contract':contract,'design_artifact':{}}
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps(wrong))))
    with pytest.raises(ValueError):generate_spec(model,context,{})
    prompt=model.invoke.call_args.args[0]
    expected={'cases':[{'obligation_id':key,'spec':value} for key,value in CASES.items()]}
    assert json.dumps(expected) in prompt
    assert 'NOT test IDs' in prompt
    model.invoke.assert_called_once()
    model.invoke.return_value=ModelResponse(json.dumps(expected))
    assert generate_spec(model,context,{})[0]==expected


def test_retained_live_wrong_stage_response_cannot_be_accepted():
    path=Path(__file__).resolve().parents[1]/'docs/evidence/integrated-test-review/test-generation-failure/model-response.json'
    with pytest.raises(ValueError):validate_spec(json.loads(path.read_text()),custom_expiration_contract())


@pytest.mark.parametrize('fault',['no_approval','budget','test_created','not_restored','wrong_status'])
def test_test_replay_fails_before_any_source_write(approved_state,fault):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    state={'context':s,'status':'STOPPED_INTEGRATED_STAGE','recovery':'rolled_back','source_attempt':3}
    decision='approve'
    if fault=='no_approval':decision='reject'
    elif fault=='budget':state['source_attempt']=2
    elif fault=='test_created':state['test_hash']='unexpected'
    elif fault=='not_restored':state['recovery']='blocked'
    elif fault=='wrong_status':state['status']='AWAITING_TEST_GENERATION_APPROVAL'
    with pytest.raises(PermissionError):replay(state,'unapproved-hash',decision)
    assert (Path(s['workspace'])/SOURCE).read_text()=='original'


@pytest.mark.parametrize('fault',[None,'hash','model_content'])
def test_replay_uses_exact_reviewed_bytes_without_new_generation(approved_state,monkeypatch,fault):
    from governance import integrated_sdlc as stage
    from governance.design import design_binding
    from governance.task_orchestrator import tasks_from_plan,task_definition_hash
    from governance.semantic_plan import digest
    from governance.file_transaction import apply_transaction,finish_transaction
    from governance.audit_logger import log_event
    from governance.test_safety import sha
    from governance.test_replay import SOURCE as SERVICE,verify_replay
    s=approved_state;root=Path(s['workspace']);original=b'original-service';(root/SERVICE).write_bytes(original)
    s['design_artifact']=json.loads(json.dumps(s['design_artifact']).replace(SOURCE,SERVICE))
    s['design_hash']=design_binding(s);s['approved_design_hash']=s['design_hash'];s['plan_design_hash']=s['design_hash']
    payload=json.loads(s['plan'].replace(SOURCE,SERVICE));payload['test_stage']['allowed_new_test_files']=[stage.TEST_PATH]
    s['plan']=json.dumps(payload);s['semantic_plan_hash']=digest(s['plan']);s['tasks']=tasks_from_plan(s['plan'],'',strict=True)
    s['approved_task_hash']=task_definition_hash(s['tasks']);s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    s.update(authorize(s))
    code=b'@Autowired\npublic UrlService(UrlRepository repository) {this(repository, java.time.Clock.systemUTC());}'
    old=apply_transaction({SERVICE:code},{SERVICE:original},root,[SERVICE],s['workflow_id'])
    model={'files':[{'path':SERVICE,'content':code.decode()}]}
    old.update({'model_proposal':model,'proposal':{'edits':[{'path':SERVICE,'old_text':original.decode(),'new_text':code.decode()}]},'raw_response':json.dumps(model)})
    finish_transaction(old['transaction_id'],root,[SERVICE],passed=False)
    log_event(s['workflow_id'],'fixture','DESIGN_SOURCE_CORRECTION_VERIFIED',{'source_hash':sha(code),
        'transaction_id':old['transaction_id'],'authorization_hash':s['plan_authorization']['authorization_hash']})
    state={'context':s,'status':'STOPPED_INTEGRATED_STAGE','recovery':'rolled_back','source_attempt':3,'transaction':old,
        'source_validation_history':[],'baseline_validation':{'cases':{'Original#case':'passed'}},'evidence':str(root/'fixture-evidence'),'cache':'fixture'}
    monkeypatch.setattr(stage,'maven',lambda *args:{'exit_code':0,'cases':{'Original#case':'passed'}})
    if fault=='model_content':old['model_proposal']={'files':[{'path':SERVICE,'content':'tampered'}]}
    if fault:
        with pytest.raises(PermissionError):replay(state,'wrong' if fault=='hash' else sha(code),'approve')
        assert (root/SERVICE).read_bytes()==original
    else:
        result=replay(state,sha(code),'approve');assert result['status']=='TEST_STAGE_REPLAY_READY'
        assert result['source_attempt']==3 and (root/SERVICE).read_bytes()==code
        assert result['transaction']['generation_origin'].endswith('no new generation')
        verify_replay(result)
        with pytest.raises(PermissionError):replay(state,sha(code),'approve')
        finish_transaction(result['transaction']['transaction_id'],root,[SERVICE],passed=False)
