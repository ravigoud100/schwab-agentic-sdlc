"""Sequential, opt-in continuation of a prospectively approved engineering plan.

One implementation task, one additive expiration test, no historical patch replay.
Failures compensate and stop; recovery requiring another plan returns to review.
"""
import base64
import difflib
import json
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from agents.implementation_agent import implement_task
from agents.test_generation_agent import generate_spec, render, CASES, validate_spec
from governance.audit_logger import log_event
from governance.plan_lineage import approved, before_execution, link_tests, verify_test_link, source_journal, events, receipt
from governance.semantic_plan import digest
from governance.test_safety import inventory, preserve, load_baseline, assert_integrity, new_test_target, sha
from governance.file_transaction import finish_transaction, save_journal, sync_directory
from governance.test_stage import maven, require_results

TEST_PATH='url-shortener/src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java'


def log(state, event, details):
    log_event(state['context']['workflow_id'], 'integrated_sdlc', event, details)


def scope(state):
    auth=approved(state['context'])
    if auth['body']['test_additions'] != [TEST_PATH]:
        raise PermissionError('Integrated expiration renderer requires its exact authorized test path')
    return {'authorization_hash': auth['authorization_hash'], 'test_path': TEST_PATH,
            'execution_hash': state['transaction']['execution_receipt']['execution_hash'],
            'policy': 'immutable-original-project;one-additive-vetted-expiration-test'}


def compensate(state, error):
    recovery='no_source_writes'
    try:
        context=state['context']; root=Path(context['workspace'])
        test=state.get('test_hash');target=root/TEST_PATH
        if test and (not target.is_file() or target.is_symlink() or sha(target.read_bytes())!=test):
            raise PermissionError('Generated test changed externally; review required')
        tx=state.get('transaction')
        if tx:
            finish_transaction(tx['transaction_id'],root,tx['paths'],passed=False)
            if test:target.unlink();sync_directory(target.parent)
            recovery='rolled_back'
        journal=Path(state['evidence'])/'integrated-test-journal.json'
        if journal.exists():
            record=json.loads(journal.read_text());record['status']=recovery;save_journal(journal,record)
    except Exception as failure:
        recovery='blocked: '+str(failure)
    log(state,'INTEGRATED_STAGE_STOPPED',{'error':str(error),'recovery':recovery})
    return {**state,'status':'STOPPED_INTEGRATED_STAGE','error':str(error),'recovery':recovery}


def entry(state):
    try:
        c=state['context'];auth=approved(c)
        tasks=[t for t in c['tasks'] if t['execution_type']=='implementation']
        if len(c['tasks'])!=1 or len(tasks)!=1:raise PermissionError('Sequential milestone requires exactly one implementation task')
        task={**tasks[0],'status':'READY'}
        c={**c,'tasks':[task],'active_task_ids':[task['task_id']]}
        before_execution(c,task)
        if auth['body']['test_additions']!=[TEST_PATH]:raise PermissionError('Exact vetted additive test authorization required')
        validate_spec({'cases':[{'obligation_id':k,'spec':v} for k,v in CASES.items()]},c['obligation_contract'])
        evidence=Path(state['evidence']).resolve(strict=True)
        if evidence.is_relative_to(Path(c['workspace']).resolve()):raise PermissionError('Evidence must be outside editable workspace')
        baseline=preserve(c['workspace'],evidence/'originals')
        state={**state,'context':c,'baseline_hash':baseline}
        baseline_result=maven(c['workspace'],evidence,'baseline',state['cache'])
        if baseline_result['exit_code'] or not baseline_result['cases'] or any(v!='passed' for v in baseline_result['cases'].values()):
            raise PermissionError('Original tests must genuinely execute and pass')
        assert_integrity(c['workspace'],load_baseline(evidence/'originals',baseline))
        log(state,'INTEGRATED_ENTRY_PASSED',{'authorization_hash':auth['authorization_hash'], 'baseline_hash':baseline,
            'requirements_hash':c['requirements_hash'],'design_hash':c['approved_design_hash'],'validation':baseline_result})
        return {**state,'baseline_validation':baseline_result,'status':'INTEGRATED_ENTRY_PASSED'}
    except Exception as error:return compensate(state,error)


def implementation(state,model):
    current=state
    try:
        c=state['context'];tx=implement_task(c['tasks'][0],Path(c['workspace']),c,provider=model)
        current={**state,'transaction':tx,'context':{**c,'implementation_transaction':tx}}
        log(state,'INTEGRATED_IMPLEMENTATION_APPLIED',{'transaction_id':tx['transaction_id'],
            'execution_hash':tx['execution_receipt']['execution_hash'],'proposal_hash':digest(tx['proposal'])})
        return {**current,'status':'IMPLEMENTATION_APPLIED'}
    except Exception as error:return compensate(current,error)


def assert_applied(state):
    c=state['context'];auth=approved(c);tx=state['transaction'];execution=tx['execution_receipt']
    receipt(c,'ENGINEERING_EXECUTION_BOUND',execution)
    if execution['execution_hash']!=digest(execution['body']) or execution['body']['authorization_hash']!=auth['authorization_hash']:
        raise PermissionError('Execution binding changed')
    journal=source_journal(c['workspace'],tx['transaction_id'])
    if journal['status']!='applied' or journal['workflow_id']!=c['workflow_id']:
        raise PermissionError('Existing applied implementation transaction required')
    expected=execution['body']['expected_project_hashes']
    if {p:sha(v) for p,v in inventory(c['workspace']).items()}!=expected:
        raise PermissionError('Source/test/build inventory changed after implementation')
    return journal


def source_validation(state):
    """Compile/run retained tests before asking to generate additive tests.

    Corrections cannot change the plan/task/scope. A new receipt is required for
    any such change; before_execution enforces it on every retry.
    """
    attempt=state.get('source_attempt',0)
    try:
        assert_applied(state)
        attempt=state.get('source_attempt',0)+1
        phase=f'implementation-{attempt}'
        record=maven(state['context']['workspace'],state['evidence'],phase,state['cache'])
        assert_applied(state)
        passed=(record['exit_code']==0 and record['cases']==state['baseline_validation']['cases'])
        if passed and state.get('required_clock_delegation'):
            from governance.design_correction import assert_delegation
            assert_delegation((Path(state['context']['workspace'])/'url-shortener/src/main/java/url_shortener/UrlService.java').read_text())
        evidence={'attempt':attempt,'phase':phase,'validation':record,
                  'transaction_id':state['transaction']['transaction_id'],
                  'authorization_hash':state['context']['plan_authorization']['authorization_hash']}
        history=state.get('source_validation_history',[])+[evidence]
        if passed:
            log(state,'IMPLEMENTATION_SOURCE_GATE_PASSED',evidence)
            log(state,'AWAITING_TEST_GENERATION_APPROVAL',{'transaction_id':state['transaction']['transaction_id'],
                'authorization_hash':evidence['authorization_hash']})
            return {**state,'source_attempt':attempt,'source_validation_history':history,'status':'AWAITING_TEST_GENERATION_APPROVAL'}
        log(state,'IMPLEMENTATION_SOURCE_GATE_FAILED',evidence)
        stopped=compensate(state,'Implementation compilation/original-test gate failed')
        if stopped['recovery']!='rolled_back' or attempt>=3:
            return {**stopped,'source_attempt':attempt,'source_validation_history':history}
        c=state['context'];output=(Path(state['evidence'])/phase/'maven.log').read_text()
        c={**c,'previous_implementation_proposal':state['transaction']['proposal'],
           'previous_validation_result':{'passed':False,'exit_code':record['exit_code'],'output':output},
           'failure_context':{'category':'java_compilation_or_original_tests','validation':record,
                              'message':'Correct only the existing approved proposal. No plan, task or file-scope change is authorized.'},
           'implementation_transaction':{}}
        before_execution(c,c['tasks'][0])
        log(state,'INTEGRATED_SAME_PLAN_RETRY',{'attempt':attempt+1,'plan_hash':digest(c['plan']),
            'authorization_hash':c['plan_authorization']['authorization_hash'],'diagnostic_hash':sha(output.encode())})
        return {**state,'context':c,'transaction':{},'source_attempt':attempt,
                'source_validation_history':history,'status':'SOURCE_RETRY'}
    except Exception as error:return compensate({**state,'source_attempt':attempt},error)


def test_review(state):
    try:
        assert_applied(state);policy=scope(state)
        if not state.get('source_validation_history') or state['source_validation_history'][-1]['transaction_id']!=state['transaction']['transaction_id']:
            raise PermissionError('Current source transaction must pass its original-test/compilation gate first')
    except Exception as error:return compensate(state,error)
    hashed=digest(policy)
    decision=interrupt({'type':'INTEGRATED_TEST_GENERATION_APPROVAL','scope':policy,'scope_hash':hashed,
        'message':'Approve only additive constrained tests against this existing implementation transaction; no patch replay.'})
    if decision!='approve':return compensate(state,'Test-generation approval rejected')
    log(state,'TEST_SCOPE_APPROVED',{'scope_hash':hashed,'scope':policy})
    return {**state,'scope':policy,'approved_scope_hash':hashed,'status':'TEST_SCOPE_APPROVED'}


def tests(state,model):
    current=state
    try:
        assert_applied(state)
        if state.get('approved_scope_hash')!=digest(scope(state)):raise PermissionError('Test scope approval changed')
        receipt(state['context'],'TEST_SCOPE_APPROVED',{'scope_hash':state['approved_scope_hash'],'scope':state['scope']})
        c=state['context'];root=Path(c['workspace']);evidence=Path(state['evidence'])
        target=new_test_target(root,TEST_PATH)
        spec,prompt,raw=generate_spec(model,c,state['scope']);data=render(spec,c['obligation_contract'])
        for name,content in [('generation-prompt.txt',prompt),('generation-response.json',raw)]:
            with (evidence/name).open('x') as stream:stream.write(content)
        assert_applied(state);new_test_target(root,TEST_PATH)
        from governance.policy_engine import enforce
        enforce({TEST_PATH:data},{TEST_PATH:None},root,c['workflow_id'],allow_new_test_files=[TEST_PATH])
        journal={'status':'prepared','test_path':TEST_PATH,'test_hash':sha(data),
            'scope_hash':state['approved_scope_hash'],'source_transaction_id':state['transaction']['transaction_id']}
        save_journal(evidence/'integrated-test-journal.json',journal)
        fd,name=tempfile.mkstemp(prefix='.integrated-test-',dir=target.parent)
        temporary=Path(name)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.link(temporary,target)
            current={**state,'test_hash':sha(data)}
            sync_directory(target.parent)
        finally:temporary.unlink(missing_ok=True)
        journal['status']='applied';save_journal(evidence/'integrated-test-journal.json',journal)
        log(current,'TEST_FILE_CREATED',{'path':TEST_PATH,'sha256':sha(data),'scope_hash':state['approved_scope_hash'],
            'source_transaction':state['transaction']['transaction_id']})
        return {**current,'status':'ADDITIVE_TEST_CREATED'}
    except Exception as error:return compensate(current,error)


def validation(state):
    try:
        c=state['context'];root=Path(c['workspace']);tx=state['transaction'];execution=tx['execution_receipt']
        expected={**execution['body']['expected_project_hashes'],TEST_PATH:state['test_hash']}
        def intact():
            if {p:sha(v) for p,v in inventory(root).items()}!=expected:raise PermissionError('Unauthorized test/source/build changes')
        intact()
        record=maven(root,state['evidence'],'positive',state['cache'])
        intact();require_results(record,state['baseline_validation']['cases'])
        finish_transaction(tx['transaction_id'],root,tx['paths'],passed=True)
        log(state,'TEST_STAGE_EXIT_PASSED',{'scope_hash':state['approved_scope_hash'],'test_hash':state['test_hash'],'validation':record})
        link=link_tests(c,execution,TEST_PATH,state['test_hash'],state['approved_scope_hash'],record)
        journal=Path(state['evidence'])/'integrated-test-journal.json'
        body=json.loads(journal.read_text());body['status']='validated';save_journal(journal,body)
        origin={**state,'profile':'integrated-expiration-v1','validation':record,'engineering_test_link':link,
                'source_transaction':tx['transaction_id'],'status':'AWAITING_TEST_RESULTS_REVIEW'}
        manifest=verify(origin)
        log(state,'INTEGRATED_EVIDENCE_VERIFIED',{'bundle_hash':digest(manifest),'lineage_hash':link['lineage_hash']})
        return {**origin,'origin':origin,'bundle_hash':digest(manifest),'run_id':c['workflow_id'],
                'output_dir':state['evidence']}
    except Exception as error:return compensate(state,error)


def maven_record(evidence,phase,*,allow_failed=False):
    directory=Path(evidence)/phase;path=directory/'result.json';record=json.loads(path.read_text());cases={};hashes={}
    reports=sorted(directory.glob('TEST-*.xml'))
    if not reports and not (allow_failed and record['exit_code']!=0):raise PermissionError('Missing genuine Surefire reports')
    for report in reports:
        hashes[str(report)]=sha(report.read_bytes())
        for case in ET.parse(report).getroot().iter('testcase'):
            key=case.attrib['classname']+'#'+case.attrib['name']
            if key in cases:raise PermissionError('Duplicate test identity')
            cases[key]='failure' if case.find('failure') is not None else 'error' if case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
    if cases!=record['cases']:raise PermissionError('Surefire/checkpoint summary mismatch')
    command=record['command']
    if len(command)!=5 or command[:2]!=['./mvnw','-o'] or not command[2].startswith('-Dmaven.repo.local=') or command[3:]!=['clean','test']:
        raise PermissionError('Unsupported Maven command or exclusions')
    log_path=directory/'maven.log'
    if not log_path.read_bytes():raise PermissionError('Maven execution log missing')
    for p in (path,log_path):hashes[str(p)]=sha(p.read_bytes())
    return record,hashes


def verify(state):
    c=state['context'];tx=state['transaction'];root=Path(c['workspace']);evidence=Path(state['evidence'])
    plan_hash=verify_test_link(c,tx['execution_receipt'],state['engineering_test_link'],state['source_transaction'],
        TEST_PATH,state['test_hash'],state['approved_scope_hash'],state['validation'])
    if state['approved_scope_hash']!=digest(scope(state)):raise PermissionError('Test scope changed')
    receipt(c,'TEST_SCOPE_APPROVED',{'scope_hash':state['approved_scope_hash'],'scope':state['scope']})
    baseline=load_baseline(evidence/'originals',state['baseline_hash'])
    if {p:sha(v) for p,v in baseline.items()}!=approved(c)['body']['project_baseline']:
        raise PermissionError('Original snapshots differ from plan baseline')
    spec=json.loads((evidence/'generation-response.json').read_text())
    if render(spec,c['obligation_contract'])!=(root/TEST_PATH).read_bytes():raise PermissionError('Generated test profile changed')
    journal=source_journal(root,state['source_transaction'])
    changes={e['path']:base64.b64decode(e['expected_content'],validate=True) for e in journal['files']}
    for e in journal['files']:
        if sha(changes[e['path']])!=e['expected_current_hash'] or baseline[e['path']]!=base64.b64decode(e['original_content'],validate=True):
            raise PermissionError('Source snapshot/hash mismatch')
    assert_integrity(root,baseline,{**changes,TEST_PATH:render(spec,c['obligation_contract'])})
    records={};artifacts={}
    for phase in ('baseline','positive'):
        records[phase],hashes=maven_record(evidence,phase);artifacts.update(hashes)
    if records['baseline']!=state['baseline_validation'] or records['positive']!=state['validation']:raise PermissionError('Maven records changed')
    if records['baseline']['exit_code']!=0:raise PermissionError('Baseline validation failed')
    require_results(records['positive'],records['baseline']['cases'])
    for item in state['source_validation_history']:
        checked,hashes=maven_record(evidence,item['phase'],allow_failed=True);artifacts.update(hashes)
        if checked!=item['validation']:raise PermissionError('Source-gate validation record changed')
    last=state['source_validation_history'][-1]
    if last['transaction_id']!=state['source_transaction'] or last['validation']['exit_code']!=0 or last['validation']['cases']!=records['baseline']['cases']:
        raise PermissionError('Current transaction has no passing original-test source gate')
    creation=json.loads((evidence/'integrated-test-journal.json').read_text())
    if creation!={'status':'validated','test_path':TEST_PATH,'test_hash':state['test_hash'],
        'scope_hash':state['approved_scope_hash'],'source_transaction_id':state['source_transaction']}:
        raise PermissionError('Additive test journal mismatch')
    for p in (evidence/'integrated-test-journal.json',evidence/'generation-prompt.txt',evidence/'generation-response.json',
              root/'.agent-transactions'/(state['source_transaction']+'.json')):artifacts[str(p)]=sha(p.read_bytes())
    cutoff=receipt(c,'ENGINEERING_TESTS_BOUND',state['engineering_test_link'])
    audit=[e for e in events(c) if e['id']<=cutoff]
    actual_diff=''.join(''.join(difflib.unified_diff(baseline[p].decode().splitlines(True),data.decode().splitlines(True),fromfile=p,tofile=p)) for p,data in changes.items())
    return {'profile':'integrated-expiration-v1','workflow_id':c['workflow_id'],'workspace':str(root.resolve()),
        'requirements_hash':c['requirements_hash'],'requirement_version':c['requirement_version'],
        'approved_design_hash':c['approved_design_hash'],'approved_contract_hash':c['approved_contract_hash'],
        'approved_implementation_plan_hash':plan_hash,'implementation_hash':digest({p:sha(v) for p,v in changes.items()}),
        'generated_test_hash':state['test_hash'],'test_path':TEST_PATH,'source_transaction':state['source_transaction'],
        'records':records,'artifacts':artifacts,'requirements':c['structured_requirements'],
        'clarification_answers':c['clarification_answers'],'approved_design':c['design_artifact'],
        'approved_contract':c['obligation_contract'],'approved_plan':c['plan'],'actual_diff':actual_diff,
        'audit_events':audit,'test_scope_hash':state['approved_scope_hash']}


def build_workflow(model):
    from governance.documentation_release import review_tests,documentation,evaluate,final_review
    graph=StateGraph(dict)
    from governance.design_correction import begin_continuation
    from governance.test_replay import verify_replay
    graph.add_node('correction_entry',begin_continuation)
    graph.add_node('test_replay_entry',verify_replay)
    graph.add_node('entry',entry);graph.add_node('implementation',lambda s:implementation(s,model))
    graph.add_node('source_validation',source_validation)
    graph.add_node('test_generation_review',test_review);graph.add_node('test_generation',lambda s:tests(s,model))
    graph.add_node('maven_validation',validation);graph.add_node('test_results_review',review_tests)
    graph.add_node('documentation',lambda s:documentation(s,model));graph.add_node('readiness',evaluate)
    graph.add_node('final_review',final_review)
    graph.add_conditional_edges(START,lambda s:'replay' if s.get('test_replay_receipt') else 'correct' if s.get('design_correction_receipt') else 'new',
        {'replay':'test_replay_entry','correct':'correction_entry','new':'entry'})
    graph.add_edge('test_replay_entry','test_generation_review')
    graph.add_edge('correction_entry','implementation')
    for node,next_node in [('entry','implementation'),('implementation','source_validation'),
        ('test_generation_review','test_generation'),('test_generation','maven_validation'),('maven_validation','test_results_review')]:
        graph.add_conditional_edges(node,lambda s:'stop' if s['status'].startswith('STOPPED') else 'next',{'stop':END,'next':next_node})
    graph.add_conditional_edges('source_validation',lambda s:'stop' if s['status'].startswith('STOPPED') else 'retry' if s['status']=='SOURCE_RETRY' else 'review',
        {'stop':END,'retry':'implementation','review':'test_generation_review'})
    graph.add_conditional_edges('test_results_review',lambda s:'next' if s['status']=='TEST_RESULTS_APPROVED' else 'stop',{'next':'documentation','stop':END})
    graph.add_conditional_edges('documentation',lambda s:'next' if s['status']=='DOCUMENTATION_VALIDATED' else 'retry' if s['status']=='DOCUMENTATION_RETRY' else 'stop',
        {'next':'readiness','retry':'documentation','stop':END})
    graph.add_edge('readiness','final_review');graph.add_edge('final_review',END)
    return graph
