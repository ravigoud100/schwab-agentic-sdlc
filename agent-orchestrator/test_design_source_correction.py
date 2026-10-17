"""Correction approvals and attempt accounting never imply additive test approval."""
import copy
import pytest
from governance.design_correction import assert_delegation,authorize_correction,verify_continuation
from governance.plan_lineage import authorize
from governance.audit_logger import log_event
from governance.semantic_plan import digest
from test_plan_lineage import approved_state,SOURCE
from test_integrated_sdlc import setup


@pytest.mark.parametrize('annotation',['@Autowired','@org.springframework.beans.factory.annotation.Autowired'])
def test_exact_approved_constructor_delegation(annotation):
    assert_delegation(annotation+'\npublic UrlService(UrlRepository repository) { this(repository, java.time.Clock.systemUTC()); }')


@pytest.mark.parametrize('body',[
    'this.repository = repository; this.clock = java.time.Clock.systemUTC();',
    '/* this(repository, java.time.Clock.systemUTC()); */',
    'this(repository, java.time.Clock.systemDefaultZone());',
    'this(repository, java.time.Clock.systemUTC()); extra();',
])
def test_direct_initialization_or_comment_cannot_satisfy_delegation(body):
    with pytest.raises(ValueError):assert_delegation('@Autowired\npublic UrlService(UrlRepository repository) {'+body+'}')


def test_missing_constructor_annotation_rejected():
    with pytest.raises(ValueError):assert_delegation('public UrlService(UrlRepository repository) { this(repository, java.time.Clock.systemUTC()); }')


@pytest.mark.parametrize('decision,attempt',[('reject',2),('approve',3)])
def test_missing_approval_or_exhausted_budget_cannot_start_correction(approved_state,decision,attempt):
    s=approved_state;s.update(authorize(s))
    with pytest.raises(PermissionError):authorize_correction({'context':s,'source_attempt':attempt,'status':'AWAITING_TEST_GENERATION_APPROVAL'}, {},decision)
    assert (__import__('pathlib').Path(s['workspace'])/SOURCE).read_text()=='original'


@pytest.mark.parametrize('fault',[None,'count','next','plan','required_check','receipt'])
def test_continuation_rechecks_receipt_and_remaining_budget(approved_state,fault):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    binding={'authorization_hash':s['plan_authorization']['authorization_hash'],'plan_hash':digest(s['plan']),
        'design_hash':s['approved_design_hash'],'source_attempts_used':2,'next_attempt':3}
    if fault!='receipt':log_event(s['workflow_id'],'fixture','DESIGN_SOURCE_CORRECTION_APPROVED',copy.deepcopy(binding))
    state={'context':s,'source_attempt':2,'status':'SOURCE_CORRECTION_AUTHORIZED',
        'design_correction_receipt':binding,'required_clock_delegation':True}
    if fault=='count':state['source_attempt']=0
    elif fault=='next':binding['next_attempt']=4
    elif fault=='plan':binding['plan_hash']='changed'
    elif fault=='required_check':state['required_clock_delegation']=False
    if fault:
        with pytest.raises(PermissionError):verify_continuation(state)
    else:assert verify_continuation(state) is state


def test_correction_approval_is_single_use_even_if_baseline_is_restored(approved_state):
    from governance.design_correction import begin_continuation
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    binding={'authorization_hash':s['plan_authorization']['authorization_hash'],'plan_hash':digest(s['plan']),
        'design_hash':s['approved_design_hash'],'source_attempts_used':2,'next_attempt':3}
    log_event(s['workflow_id'],'fixture','DESIGN_SOURCE_CORRECTION_APPROVED',binding)
    state={'context':s,'source_attempt':2,'status':'SOURCE_CORRECTION_AUTHORIZED',
        'design_correction_receipt':binding,'required_clock_delegation':True}
    begin_continuation(state)
    with pytest.raises(PermissionError,match='already consumed'):begin_continuation(state)


@pytest.mark.parametrize('compliant',[False,True])
def test_third_attempt_keeps_budget_and_requires_delegation_despite_green_maven(approved_state,monkeypatch,compliant):
    from pathlib import Path
    from governance import integrated_sdlc as stage
    from governance.file_transaction import apply_transaction,finish_transaction
    s=approved_state;root=Path(s['workspace']);path='url-shortener/src/main/java/url_shortener/UrlService.java'
    (root/path).write_bytes(b'original-service')
    s.update(authorize(s))
    body='this(repository, java.time.Clock.systemUTC());' if compliant else 'this.repository=repository;'
    new=('@Autowired\npublic UrlService(UrlRepository repository) {'+body+'}').encode()
    tx=apply_transaction({path:new},{path:b'original-service'},root,[path],s['workflow_id'])
    monkeypatch.setattr(stage,'assert_applied',lambda state:None)
    monkeypatch.setattr(stage,'maven',lambda *args:{'exit_code':0,'cases':{'Original#case':'passed'}})
    result=stage.source_validation({'context':s,'source_attempt':2,'required_clock_delegation':True,
        'baseline_validation':{'cases':{'Original#case':'passed'}},'evidence':str(root/'unused'),
        'transaction':tx,'cache':'fixture'})
    assert result['source_attempt']==3
    if compliant:
        assert result['status']=='AWAITING_TEST_GENERATION_APPROVAL'
        finish_transaction(tx['transaction_id'],root,[path],passed=False)
    else:
        assert result['status']=='STOPPED_INTEGRATED_STAGE' and result['recovery']=='rolled_back'
        assert (root/path).read_bytes()==b'original-service'


def test_corrected_nested_workflow_resumes_test_review_without_rechecking_original_baseline(setup,tmp_path):
    import json,sqlite3
    from pathlib import Path
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command
    import main
    from governance.design import design_binding
    from governance.task_orchestrator import tasks_from_plan
    from governance.test_safety import sha
    from test_integrated_sdlc import start
    s,model,calls=setup;service='url-shortener/src/main/java/url_shortener/UrlService.java'
    (Path(s['workspace'])/service).write_text('original')
    s['design_artifact']=json.loads(json.dumps(s['design_artifact']).replace(SOURCE,service))
    s['design_hash']=design_binding(s);s['approved_design_hash']=s['design_hash'];s['plan_design_hash']=s['design_hash']
    s['plan']=s['plan'].replace(SOURCE,service);s['semantic_plan_hash']=digest(s['plan']);s['tasks']=tasks_from_plan(s['plan'],'',strict=True)
    old_invoke=model.invoke.side_effect;outputs=iter([
        '@Autowired\npublic UrlService(UrlRepository repository) {this.repository=repository;}',
        '@Autowired\npublic UrlService(UrlRepository repository) {this(repository, java.time.Clock.systemUTC());}'])
    def invoke(prompt,*,stage):
        if stage=='implementation':
            from llm.provider import ModelResponse
            return ModelResponse(json.dumps({'edits':[{'path':service,'old_text':'original','new_text':next(outputs)}]}))
        return old_invoke(prompt,stage=stage)
    model.invoke.side_effect=invoke
    database=str(tmp_path/'correction.db')
    with SqliteSaver.from_conn_string(database) as saver:
        app,config,_=start(s,saver);app.invoke(Command(resume='approve'),config)
        with sqlite3.connect(database) as conn:
            ns=conn.execute("select distinct checkpoint_ns from checkpoints where checkpoint_ns!=''").fetchone()[0]
        child=saver.get_tuple({'configurable':{**config['configurable'],'checkpoint_ns':ns}}).checkpoint['channel_values']['__root__']
        c=child['context'];review={'review_status':'BLOCKED_DESIGN_CORRECTION_REQUIRED',
            'authorization_hash':c['plan_authorization']['authorization_hash'],'approved_design_hash':c['approved_design_hash'],
            'approved_plan_hash':digest(c['plan']),'source_transaction_id':child['transaction']['transaction_id'],
            'source_attempts_used':1,'source_path':service,'reviewed_source_hash':sha((Path(c['workspace'])/service).read_bytes()),
            'required_correction':'Use the reviewed constructor delegation.'}
        log_event(c['workflow_id'],'fixture','TEST_GENERATION_ARCHITECTURE_REVIEW_BLOCKED',review)
        correction=authorize_correction(child,review,'approve')
        app.update_state(config,{'integrated_correction_state':correction},as_node='approved')
        result=app.invoke(None,config)
        assert result['__interrupt__'][0].value['type']=='INTEGRATED_TEST_GENERATION_APPROVAL'
    # Reconstruct in a new saver: the already-applied corrected source must not
    # be compared with the old baseline again in the replayed parent wrapper.
    with SqliteSaver.from_conn_string(database) as saver:
        app=main.workflow.compile(checkpointer=saver)
        result=app.invoke(Command(resume='approve'),config)
        assert result['__interrupt__'][0].value['type']=='TEST_RESULTS_REVIEW'
    assert calls==['baseline','implementation-1','implementation-2','positive']
