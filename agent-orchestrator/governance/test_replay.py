"""One explicitly approved test-stage replay of exact, already generated source.

No model implementation call, attempt reset, plan change or historical patch use.
"""
import base64
from pathlib import Path
from governance.plan_lineage import approved,before_execution,bind_execution,source_journal,events,receipt
from governance.file_transaction import apply_transaction,finish_transaction
from governance.test_safety import sha
from governance.semantic_plan import digest
from governance.design_correction import assert_delegation
from governance.audit_logger import log_event

SOURCE='url-shortener/src/main/java/url_shortener/UrlService.java'


def replay(state,expected_hash,decision):
    from governance.integrated_sdlc import assert_applied,maven,compensate
    c=state['context'];root=Path(c['workspace']);auth=approved(c)
    if decision!='approve':raise PermissionError('Explicit exact-source/test replay approval required')
    if (state.get('status')!='STOPPED_INTEGRATED_STAGE' or state.get('recovery')!='rolled_back'
        or state.get('source_attempt')!=3 or state.get('test_hash')):
        raise PermissionError('Only the reviewed zero-test-write, exhausted-generation state supports this replay')
    if any(e['event']=='TEST_STAGE_EXACT_REPLAY_APPROVED' for e in events(c)):
        raise PermissionError('The single test-stage replay has already been used')
    before_execution(c,c['tasks'][0])
    if auth['body']['test_additions']!=['url-shortener/src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java']:
        raise PermissionError('Replay requires the exact approved additive test path')
    old=state['transaction'];journal=source_journal(root,old['transaction_id'])
    if journal['status']!='rolled_back' or old['paths']!=[SOURCE] or len(journal['files'])!=1:
        raise PermissionError('Reviewed source must be fully restored before replay')
    entry=journal['files'][0];data=base64.b64decode(entry['expected_content'],validate=True)
    original=base64.b64decode(entry['original_content'],validate=True)
    if entry['path']!=SOURCE or sha(data)!=entry['expected_current_hash'] or sha(data)!=expected_hash:
        raise PermissionError('Exact reviewed model-source hash required')
    if sha(original)!=entry['original_hash'] or original!=(root/SOURCE).read_bytes():
        raise PermissionError('Replay original bytes/hash mismatch')
    if old.get('model_proposal')!={'files':[{'path':SOURCE,'content':data.decode()}]}:
        raise PermissionError('Replay must match the actual complete-file model proposal')
    assert_delegation(data.decode())
    verified=[e for e in events(c) if e['event']=='DESIGN_SOURCE_CORRECTION_VERIFIED'
              and e['details'].get('source_hash')==expected_hash
              and e['details'].get('transaction_id')==old['transaction_id']
              and e['details'].get('authorization_hash')==auth['authorization_hash']]
    if not verified:raise PermissionError('Reviewed corrected-source evidence required')
    grant={'authorization_hash':auth['authorization_hash'],'plan_hash':digest(c['plan']),
        'source_hash':expected_hash,'replayed_from_transaction':old['transaction_id'],
        'source_generations_used':3,'source_generation_limit':3,'test_replay_number':1,
        'new_test_path':'url-shortener/src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java'}
    log_event(c['workflow_id'],'test_replay','TEST_STAGE_EXACT_REPLAY_APPROVED',grant)
    current=state;tx=None
    try:
        tx=apply_transaction({SOURCE:data},{SOURCE:original},root,[SOURCE],c['workflow_id'])
        current={**state,'transaction':tx}
        tx['execution_receipt']=bind_execution(c,c['tasks'][0],tx)
        tx.update({'proposal':old['proposal'],'model_proposal':old['model_proposal'],
            'raw_response':old['raw_response'],'response_format':'complete_files',
            'replayed_from_transaction':old['transaction_id'],'generation_origin':'exact approved model-source replay; no new generation'})
        context={**c,'implementation_transaction':tx}
        current={**current,'context':context}
        assert_applied(current)
        record=maven(root,state['evidence'],'source-replay-1',state['cache'])
        assert_applied(current)
        if record['exit_code']!=0 or record['cases']!=state['baseline_validation']['cases']:
            raise ValueError('Replayed source must pass all original Maven tests unchanged')
        assert_delegation((root/SOURCE).read_text())
        evidence={'attempt':3,'execution_type':'approved_exact_source_replay','phase':'source-replay-1',
            'validation':record,'transaction_id':tx['transaction_id'],
            'authorization_hash':auth['authorization_hash']}
        binding={'grant':grant,'execution_hash':tx['execution_receipt']['execution_hash'],
                 'transaction_id':tx['transaction_id'],'validation_hash':digest(record)}
        log_event(c['workflow_id'],'test_replay','TEST_STAGE_EXACT_REPLAY_VALIDATED',binding)
        return {**current,'source_validation_history':state['source_validation_history']+[evidence],
            'test_replay_receipt':binding,'status':'TEST_STAGE_REPLAY_READY','error':'','recovery':''}
    except Exception as error:return compensate(current,error)


def verify_replay(state):
    from governance.integrated_sdlc import assert_applied
    c=state['context'];auth=approved(c);binding=state['test_replay_receipt'];grant=binding['grant']
    receipt(c,'TEST_STAGE_EXACT_REPLAY_APPROVED',grant)
    receipt(c,'TEST_STAGE_EXACT_REPLAY_VALIDATED',binding)
    if (state.get('status')!='TEST_STAGE_REPLAY_READY' or state['source_attempt']!=3
        or grant['source_generations_used']!=3 or grant['source_generation_limit']!=3
        or grant['test_replay_number']!=1 or grant['plan_hash']!=digest(c['plan'])
        or grant['authorization_hash']!=auth['authorization_hash']
        or binding['transaction_id']!=state['transaction']['transaction_id']
        or binding['execution_hash']!=state['transaction']['execution_receipt']['execution_hash']):
        raise PermissionError('Replay identity, execution or attempt accounting changed')
    assert_applied(state)
    if sha((Path(c['workspace'])/SOURCE).read_bytes())!=grant['source_hash']:
        raise PermissionError('Replayed source differs from explicit approval')
    return state
