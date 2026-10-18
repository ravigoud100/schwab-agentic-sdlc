import copy,json
from pathlib import Path
from unittest.mock import Mock,patch
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from agents.test_generation_agent import CASES,generate_spec,render,validate_spec
from governance.semantic_plan import custom_expiration_contract
from governance.test_safety import preserve,load_baseline,assert_integrity,new_test_target
from governance.test_stage import execute,scope_hash,require_results,build_test_workflow
from llm.provider import ModelResponse


def spec():return {'cases':[{'obligation_id':k,'spec':v} for k,v in CASES.items()]}


def project(tmp_path):
    root=tmp_path/'workspace';root.mkdir()
    for path,content in {'src/test/java/demo/OriginalTest.java':'assertEquals(1,1);',
                         'src/main/java/demo/Service.java':'source','pom.xml':'build','mvnw':'wrapper'}.items():
        p=root/'url-shortener'/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
    return root


@pytest.mark.parametrize('attack',['delete','disable','assertion','build','exclusion','extra'])
def test_original_tests_and_build_preserved_fail_closed(tmp_path,attack):
    root=project(tmp_path);baseline_dir=tmp_path/'outside';expected=preserve(root,baseline_dir)
    baseline=load_baseline(baseline_dir,expected)
    test=root/'url-shortener/src/test/java/demo/OriginalTest.java'
    if attack=='delete':test.unlink()
    if attack=='disable':test.write_text('@Disabled '+test.read_text())
    if attack=='assertion':test.write_text('assertTrue(true);')
    if attack=='build':(root/'url-shortener/pom.xml').write_text('skipTests')
    if attack=='exclusion':(root/'url-shortener/.mvn').mkdir();(root/'url-shortener/.mvn/maven.config').write_text('-DskipTests')
    if attack=='extra':(test.parent/'HiddenTest.java').write_text('extra')
    with pytest.raises(PermissionError):assert_integrity(root,baseline)
    assert load_baseline(baseline_dir,expected)==baseline


def test_baseline_tampering_and_internal_snapshot_rejected(tmp_path):
    root=project(tmp_path)
    with pytest.raises(PermissionError):preserve(root,root/'snapshot')
    d=tmp_path/'outside';expected=preserve(root,d);(d/'baseline.json').write_text('{}')
    with pytest.raises(PermissionError):load_baseline(d,expected)


@pytest.mark.parametrize('attack',['missing','duplicate','weaken','code','disable','unknown_contract'])
def test_untrusted_model_cannot_weaken_or_inject_test_source(attack):
    payload=copy.deepcopy(spec());contract=custom_expiration_contract()
    if attack=='missing':payload['cases'].pop()
    if attack=='duplicate':payload['cases'].append(payload['cases'][0])
    if attack=='weaken':payload['cases'][0]['spec']['expected']='anything'
    if attack=='code':payload['java']='Runtime.getRuntime().exec(...)'
    if attack=='disable':payload['cases'][0]['disabled']=True
    if attack=='unknown_contract':contract['selected_answer']='other'
    with pytest.raises(ValueError):render(payload,contract)


def test_generator_receives_authoritative_context_and_renderer_owns_assertions():
    context={'structured_requirements':{'problem':'approved'},'clarification_answers':{'A1':'approved choice'},
             'obligation_contract':custom_expiration_contract(),'design_artifact':{'decision':'approved design'}}
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps(spec()))))
    payload,prompt,_=generate_spec(model,context,{'architecture':'Clock seam approved'})
    for value in ('approved choice','approved design','Clock seam approved','strictly_after_now'):assert value in prompt
    assert model.invoke.call_args.kwargs['stage']=='test_generation'
    source=render(payload,context['obligation_contract']).decode()
    for value in ('Clock.fixed','assertThrows','assertNull','never()).save','BAD_REQUEST','equalNowExpiration'):
        assert value in source


@pytest.mark.parametrize('path',['../Escape.java','url-shortener/pom.xml','url-shortener/src/main/java/Bad.java',
                                  'url-shortener/src/test/java/demo/OriginalTest.java'])
def test_only_new_authorized_test_paths(tmp_path,path):
    root=project(tmp_path)
    with pytest.raises(PermissionError):new_test_target(root,path)


def state(tmp_path):
    root=project(tmp_path);evidence=tmp_path/'evidence';evidence.mkdir()
    context={'workflow_id':'test-policy','workspace':str(root),'requirements_hash':'req','approved_design_hash':'design',
             'obligation_contract':custom_expiration_contract(),
             'structured_requirements':{'normalized_problem':'expiration'},
             'clarification_answers':{'A1':'Custom expiration timestamp'},'design_artifact':{'decision':'Clock'}}
    scope={'production_file':'url-shortener/src/main/java/demo/Service.java',
           'new_test_file':'url-shortener/src/test/java/demo/GeneratedExpirationBoundaryTest.java','policy':'preserve','generator':'constrained'}
    result={'context':context,'scope':scope,'old_source':b'source','new_source':b'revised','evidence':str(evidence),'cache':str(tmp_path/'cache')}
    result['approved_scope_hash']=scope_hash(result);return result


def test_no_approval_no_model_or_maven_or_source_writes(tmp_path):
    s=state(tmp_path);s['approved_scope_hash']='stale';model=Mock()
    with patch('governance.test_stage.assert_design_current'),patch('governance.test_stage.log_event'),patch('governance.test_stage.maven') as maven:
        result=execute(s,model)
    assert result['status']=='STOPPED_TEST_STAGE' and result['recovery']=='no_source_writes'
    maven.assert_not_called();model.invoke.assert_not_called()
    assert (Path(s['context']['workspace'])/s['scope']['production_file']).read_bytes()==b'source'


def test_invalid_model_proposal_rejected_before_any_source_write(tmp_path):
    s=state(tmp_path)
    model=Mock(invoke=Mock(return_value=ModelResponse('{"cases":[],"java":"@Disabled"}')))
    with patch('governance.test_stage.assert_design_current'),patch('governance.test_stage.log_event'),patch(
        'governance.test_stage.maven',return_value={'exit_code':0,'cases':{'Original#test':'passed'}}),patch('governance.test_stage.apply_transaction') as apply:
        result=execute(s,model)
    assert result['status']=='STOPPED_TEST_STAGE' and result['recovery']=='no_source_writes'
    model.invoke.assert_called_once()
    apply.assert_not_called()


def test_external_test_weakening_during_generation_blocks_first_write(tmp_path):
    s=state(tmp_path);root=Path(s['context']['workspace'])
    def invoke(*args,**kwargs):
        (root/'url-shortener/src/test/java/demo/OriginalTest.java').write_text('@Disabled')
        return ModelResponse(json.dumps(spec()))
    with patch('governance.test_stage.assert_design_current'),patch('governance.test_stage.log_event'),patch(
        'governance.test_stage.maven',return_value={'exit_code':0,'cases':{'Original#test':'passed'}}),patch('governance.test_stage.apply_transaction') as apply:
        result=execute(s,Mock(invoke=Mock(side_effect=invoke)))
    assert result['status']=='STOPPED_TEST_STAGE';apply.assert_not_called()


@pytest.mark.parametrize('bad',['skipped','missing','error','no_negative_failure'])
def test_exit_gate_requires_real_original_and_boundary_outcomes(bad):
    original={'Original#test':'passed'}
    cases={**original,**{'url_shortener.GeneratedExpirationBoundaryTest#'+n:'passed' for n in
           ('futureExpiration','pastExpiration','equalNowExpiration','missingExpiration')}}
    if bad=='skipped':cases['Original#test']='skipped'
    if bad=='missing':cases.pop('Original#test')
    if bad=='error':cases['url_shortener.GeneratedExpirationBoundaryTest#equalNowExpiration']='error'
    with pytest.raises(PermissionError):require_results({'exit_code':1,'cases':cases},original,negative=True)


def test_test_scope_review_interrupt_rejects_without_execution(tmp_path):
    s=state(tmp_path)
    with patch('governance.test_stage.assert_design_current'),patch('governance.test_stage.log_event'),patch('governance.test_stage.execute') as execute_stage,SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app=build_test_workflow(Mock()).compile(checkpointer=saver);config={'configurable':{'thread_id':'testing'}}
        paused=app.invoke(s,config=config)
        assert paused['__interrupt__'][0].value['type']=='TEST_STAGE_APPROVAL'
        result=app.invoke(Command(resume='reject'),config=config)
        assert result['status']=='STOPPED_TEST_SCOPE_REJECTED';execute_stage.assert_not_called()


@pytest.mark.parametrize('failure',['publication','positive_validation','negative_error','external_generated_edit'])
def test_failed_test_stage_compensates_or_preserves_external_changes(tmp_path,failure):
    from governance import audit_logger
    s=state(tmp_path)
    s['new_source']=b'class Service { boolean invalid = !expiresAt.isAfter(java.time.Instant.now(clock)); }'
    s['approved_scope_hash']=scope_hash(s)
    original={'Original#test':'passed'}
    cases={**original,**{'url_shortener.GeneratedExpirationBoundaryTest#'+n:'passed' for n in
            ('futureExpiration','pastExpiration','equalNowExpiration','missingExpiration')}}
    def maven(workspace,evidence,label,cache):
        if label=='baseline':return {'exit_code':0,'cases':original}
        result={'exit_code':0,'cases':dict(cases)}
        if label=='negative':
            result['exit_code']=1
            result['cases']['url_shortener.GeneratedExpirationBoundaryTest#equalNowExpiration']='error' if failure=='negative_error' else 'failure'
        if label=='positive':
            if failure=='external_generated_edit':
                (Path(workspace)/s['scope']['new_test_file']).write_text('external data')
            result={'exit_code':1,'cases':{}}
        return result
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps(spec()))))
    with patch('governance.test_stage.assert_design_current'),patch.object(audit_logger,'DB_PATH',tmp_path/'audit.db'),patch('governance.test_stage.maven',side_effect=maven):
        if failure=='publication':
            with patch('governance.test_stage.os.link',side_effect=OSError('publication failure')):result=execute(s,model)
        else:result=execute(s,model)
    assert result['status']=='STOPPED_TEST_STAGE'
    root=Path(s['context']['workspace']);source=root/s['scope']['production_file'];test=root/s['scope']['new_test_file']
    assert (root/'url-shortener/src/test/java/demo/OriginalTest.java').read_text()=='assertEquals(1,1);'
    if failure=='external_generated_edit':
        assert result['recovery'].startswith('blocked:') and test.read_text()=='external data'
        assert source.read_bytes()==s['new_source']
    else:
        assert result['recovery']=='rolled_back' and source.read_bytes()==s['old_source'] and not test.exists()
