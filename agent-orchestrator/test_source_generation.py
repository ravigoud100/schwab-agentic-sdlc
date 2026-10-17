"""Regression cases derived from retained live proposals, not invented compiler errors."""
import base64
import copy
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from agents.source_generation import complete_source_edits,complete_source_prompt
from agents.implementation_agent import implement_task
from governance.plan_lineage import authorize
from governance.file_transaction import finish_transaction
from llm.provider import ModelResponse
from test_plan_lineage import approved_state,SOURCE,TEST
from test_integrated_sdlc import setup,start
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

EVIDENCE=Path(__file__).resolve().parents[1]/'docs/evidence/integrated-sdlc'
JOURNALS=[j for folder in ('failed-live','context-failed-live') for j in json.loads((EVIDENCE/folder/'journals.json').read_text())]


@pytest.mark.parametrize('journal',JOURNALS,ids=[j['transaction_id'] for j in JOURNALS])
def test_retained_partial_constructor_edit_explains_compiler_line_110(journal):
    entry=journal['files'][0]
    original=base64.b64decode(entry['original_content']).decode()
    failed=base64.b64decode(entry['expected_content']).decode()
    anchor='    public UrlService(UrlRepository repository) {'
    suffix=original.split(anchor,1)[1]
    assert failed.endswith(suffix)
    assert failed.splitlines()[109]=='        this.repository = repository;'
    assert failed.count('public ShortUrl createShortUrl(')==2
    assert '@org.springframework.beans.factory.annotation.Autowired\n    public UrlService(UrlRepository repository, java.time.Clock clock)' in failed
    # Full-source transport cannot append an unconsumed original body.
    edits=complete_source_edits({'files':[{'path':entry['path'],'content':failed}]},{entry['path']:original})
    assert edits[0]['old_text']==original
    assert original.replace(edits[0]['old_text'],edits[0]['new_text'],1)==failed


def test_full_source_recovery_receives_actual_failed_materialization_and_full_log():
    j=JOURNALS[-1]['files'][0];source=base64.b64decode(j['original_content']).decode();bad=base64.b64decode(j['expected_content']).decode()
    old='    public UrlService(UrlRepository repository) {'
    # Reconstruct the actual recorded partial proposal from the immutable suffix.
    prefix,suffix=source.split(old,1);replacement=bad[len(prefix):len(bad)-len(suffix)]
    log='first compiler diagnostic\n'+'diagnostic\n'*500+'last compiler diagnostic'
    context={'previous_implementation_proposal':{'edits':[{'path':j['path'],'old_text':old,'new_text':replacement}]},
        'previous_validation_result':{'output':log},'design_artifact':{'architectural_decisions':['autowire repository-only; explicit Clock overload']},
        'clarification_answers':{'A1':'Custom expiration timestamp'},'obligation_contract':{'UTC':'future only'},'plan':'approved exact plan'}
    prompt=complete_source_prompt({'allowed_files':[j['path']]},{j['path']:source},context)
    assert json.dumps({j['path']:bad}) in prompt
    assert log in prompt and 'import java.time.Instant;' in prompt
    for text in ('approved_design','clarification_answers','approved_obligation_contract','approved_plan','repository-only','ORIGINAL COMPLETE SOURCES'):
        assert text in prompt
    assert 'Recommended unique insertion anchors' not in prompt


@pytest.mark.parametrize('fault',['legacy_edits','protected_test','foreign_path','duplicate','missing','extra_field','unchanged','empty'])
def test_complete_file_protocol_fails_before_transaction(approved_state,fault):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    s['implementation_response_format']='complete_files'
    payload={'files':[{'path':SOURCE,'content':'complete changed source'}]}
    if fault=='legacy_edits':payload={'edits':[{'path':SOURCE,'old_text':'original','new_text':'bad insertion'}]}
    elif fault=='protected_test':payload['files'][0]['path']=TEST
    elif fault=='foreign_path':payload['files'][0]['path']='../escape.java'
    elif fault=='duplicate':payload['files']*=2
    elif fault=='missing':payload['files']=[]
    elif fault=='extra_field':payload['files'][0]['old_text']='original'
    elif fault=='unchanged':payload['files'][0]['content']='original'
    elif fault=='empty':payload['files'][0]['content']=''
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps(payload))))
    with pytest.raises((ValueError,PermissionError)):
        implement_task(s['tasks'][0],Path(s['workspace']),s,provider=model)
    assert (Path(s['workspace'])/SOURCE).read_text()=='original'
    assert (Path(s['workspace'])/TEST).read_text()=='original'
    assert not list((Path(s['workspace'])/'.agent-transactions').glob('*.json'))


def test_complete_file_execution_retains_receipts_exact_bytes_and_rollback(approved_state):
    s=approved_state;s.update(authorize(s));s['tasks'][0]['status']='READY';s['active_task_ids']=[s['tasks'][0]['task_id']]
    s['implementation_response_format']='complete_files';content='complete replacement\n'
    model=Mock(invoke=Mock(return_value=ModelResponse(json.dumps({'files':[{'path':SOURCE,'content':content}]}))))
    tx=implement_task(s['tasks'][0],Path(s['workspace']),s,provider=model)
    assert tx['proposal']=={'edits':[{'path':SOURCE,'old_text':'original','new_text':content}]}
    assert tx['execution_receipt'] and tx['response_format']=='complete_files'
    assert (Path(s['workspace'])/SOURCE).read_text()==content
    finish_transaction(tx['transaction_id'],Path(s['workspace']),[SOURCE],passed=False)
    assert (Path(s['workspace'])/SOURCE).read_text()=='original'


def test_integrated_complete_file_correction_stops_at_test_approval(setup,tmp_path,monkeypatch):
    from governance import integrated_sdlc as stage
    s,model,calls=setup;s['implementation_response_format']='complete_files';original_maven=stage.maven
    def invoke(prompt,*,stage):
        assert stage=='implementation'
        assert 'COMPLETE replacement Java files' in prompt
        content='correct' if 'seeded compiler failure' in prompt else 'first failing full source'
        return ModelResponse(json.dumps({'files':[{'path':SOURCE,'content':content}]}))
    model.invoke.side_effect=invoke
    def maven(*args):
        record=original_maven(*args)
        if args[2]=='implementation-1':
            record['exit_code']=1
            (Path(args[1])/args[2]/'maven.log').write_text('[ERROR] seeded compiler failure')
        return record
    monkeypatch.setattr(stage,'maven',maven)
    with SqliteSaver.from_conn_string(str(tmp_path/'workflow.db')) as saver:
        app,config,_=start(s,saver);result=app.invoke(Command(resume='approve'),config)
        assert result['__interrupt__'][0].value['type']=='INTEGRATED_TEST_GENERATION_APPROVAL'
    assert calls==['baseline','implementation-1','implementation-2']
    assert (Path(s['workspace'])/SOURCE).read_text()=='correct'
    assert len(model.invoke.call_args_list)==2
    assert not (Path(s['workspace'])/stage.TEST_PATH).exists()


def test_missing_fields_failure_and_full_diagnostics_are_retained_for_correction():
    fixed=EVIDENCE.parent/'integrated-sdlc-fix'
    first=json.loads((fixed/'implementation-1-response.txt').read_text())['files'][0]['content']
    corrected=json.loads((fixed/'implementation-2-response.txt').read_text())['files'][0]['content']
    log=(fixed/'implementation-1/maven.log').read_text()
    prompt=(fixed/'implementation-2-prompt.txt').read_text()
    assert 'variable random' in log and 'variable CHARACTERS' in log
    assert log in prompt
    assert 'private final SecureRandom random' not in first
    assert 'private static final String CHARACTERS' not in first
    assert 'private final SecureRandom random' in corrected
    assert 'private static final String CHARACTERS' in corrected
    assert corrected.count('public ShortUrl createShortUrl(')==1
    assert '@Autowired\n    public UrlService(UrlRepository repository)' in corrected
    assert '@Autowired\n    public UrlService(UrlRepository repository, java.time.Clock clock)' not in corrected
    assert 'private final java.time.Clock clock;' in corrected
    assert 'Instant.now(clock)' in corrected
    assert json.dumps(first) in prompt
