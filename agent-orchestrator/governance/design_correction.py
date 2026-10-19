"""Human-requested, same-plan source correction without resetting attempt history."""
import re
from pathlib import Path
from governance.semantic_plan import digest
from governance.plan_lineage import approved,receipt,before_execution,events
from governance.file_transaction import finish_transaction
from governance.test_safety import sha
from governance.audit_logger import log_event


def assert_delegation(source):
    matches=list(re.finditer(r'public\s+UrlService\s*\(\s*UrlRepository\s+repository\s*\)\s*\{([^{}]*)\}',source))
    if len(matches)!=1 or re.sub(r'\s+','',matches[0].group(1))!='this(repository,java.time.Clock.systemUTC());':
        raise ValueError('Approved repository-only constructor must delegate to this(repository, java.time.Clock.systemUTC())')
    prefix=source[:matches[0].start()].rstrip()
    if not re.search(r'@(?:org\.springframework\.beans\.factory\.annotation\.)?Autowired\s*$',prefix):
        raise ValueError('Repository-only constructor must retain the approved Autowired annotation')


def authorize_correction(state,review,decision):
    """Called only after explicit human correction approval; never approves tests."""
    from governance.integrated_sdlc import assert_applied
    c=state['context'];auth=approved(c)
    if decision!='approve':raise PermissionError('Explicit source correction approval required')
    if state.get('status')!='AWAITING_TEST_GENERATION_APPROVAL' or state.get('test_hash'):
        raise PermissionError('Correction must precede additive test creation')
    if not isinstance(state.get('source_attempt'),int) or not 1<=state['source_attempt']<3:
        raise PermissionError('No source correction attempt remains')
    journal=assert_applied(state)
    receipt(c,'TEST_GENERATION_ARCHITECTURE_REVIEW_BLOCKED',review)
    if (review.get('review_status')!='BLOCKED_DESIGN_CORRECTION_REQUIRED'
        or review.get('authorization_hash')!=auth['authorization_hash']
        or review.get('approved_design_hash')!=c['approved_design_hash']
        or review.get('approved_plan_hash')!=digest(c['plan'])
        or review.get('source_transaction_id')!=journal['transaction_id']
        or review.get('source_attempts_used')!=state['source_attempt']):
        raise PermissionError('Review no longer matches this exact source, plan and attempt')
    path=review['source_path'];root=Path(c['workspace'])
    if path!='url-shortener/src/main/java/url_shortener/UrlService.java' or state['transaction']['paths']!=[path]:
        raise PermissionError('Only the reviewed one-file Clock correction is supported')
    if sha((root/path).read_bytes())!=review['reviewed_source_hash']:
        raise PermissionError('Reviewed source changed')
    binding={'review_hash':digest(review),'authorization_hash':auth['authorization_hash'],
        'previous_transaction_id':journal['transaction_id'],'source_attempts_used':state['source_attempt'],
        'next_attempt':state['source_attempt']+1,'requirements_hash':c['requirements_hash'],
        'design_hash':c['approved_design_hash'],'plan_hash':digest(c['plan']),
        'source_path':path,'required_correction':review['required_correction']}
    log_event(c['workflow_id'],'design_correction','DESIGN_SOURCE_CORRECTION_APPROVED',binding)
    finish_transaction(journal['transaction_id'],root,state['transaction']['paths'],passed=False)
    rolled_id = state['transaction']['transaction_id']
    cleaned_applied = {p: v for p, v in state.get('applied_changes', {}).items() if p not in state['transaction']['paths']}
    cleaned_txs = [t for t in state.get('transactions', []) if (t['transaction_id'] if isinstance(t, dict) else t) != rolled_id]
    c = {**c, 'applied_changes': cleaned_applied, 'transactions': cleaned_txs}
    before_execution(c,c['tasks'][0])
    context={**c,'implementation_transaction':{},
        'previous_implementation_proposal':state['transaction']['proposal'],
        # Maven passed. Do not misrepresent architecture feedback as compiler errors.
        'previous_validation_result':{**c.get('previous_validation_result',{}),'passed':True,
            'exit_code':0,'output':'Previous Maven source gate passed all original tests. No compiler failure; correction requested for mandatory constructor delegation.'},
        'failure_context':{'category':'design_conformance','message':review['required_correction'],
            'review_hash':digest(review),'previous_maven_passed':True},
        'recovery_correction_strategy':'Keep the last reviewed implementation unchanged except the repository-only constructor body. Replace its two direct field assignments with exactly this(repository, java.time.Clock.systemUTC()); retain @Autowired on that overload, the explicit Clock overload, random/CHARACTERS, both injected-clock checks, imports, fields and all methods. Return the complete source. No other behavioral changes are authorized.'}
    return {**state,'context':context,'transaction':{},'transactions':cleaned_txs,'applied_changes':cleaned_applied,'status':'SOURCE_CORRECTION_AUTHORIZED',
        'design_correction_receipt':binding,'required_clock_delegation':True}


def verify_continuation(state):
    c=state['context'];binding=state['design_correction_receipt'];auth=approved(c)
    receipt(c,'DESIGN_SOURCE_CORRECTION_APPROVED',binding)
    if (state.get('status')!='SOURCE_CORRECTION_AUTHORIZED' or state.get('required_clock_delegation') is not True
        or binding['authorization_hash']!=auth['authorization_hash']
        or binding['plan_hash']!=digest(c['plan']) or binding['design_hash']!=c['approved_design_hash']
        or state['source_attempt']!=binding['source_attempts_used']
        or binding['next_attempt']!=state['source_attempt']+1 or binding['next_attempt']>3):
        raise PermissionError('Correction continuation changed or exceeded its attempt budget')
    before_execution(c,c['tasks'][0])
    return state


def begin_continuation(state):
    verify_continuation(state)
    binding=state['design_correction_receipt'];c=state['context']
    if any(row['event']=='DESIGN_SOURCE_CORRECTION_STARTED' and row['details']==binding for row in events(c)):
        raise PermissionError('This correction approval has already consumed its remaining attempt')
    log_event(c['workflow_id'],'design_correction','DESIGN_SOURCE_CORRECTION_STARTED',binding)
    return state
