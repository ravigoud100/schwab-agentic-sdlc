"""Opt-in, approval-gated expiration test stage with immutable original tests.

New files use a dedicated additive policy, not the existing-file implementation
executor. Source edits remain exact reviewed bytes and use its transaction journal.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from langgraph.graph import StateGraph,START,END
from langgraph.types import interrupt
from governance.audit_logger import log_event
from governance.design import assert_design_current
from governance.semantic_plan import digest
from governance.test_safety import inventory,preserve,load_baseline,assert_integrity,new_test_target,sha
from governance.file_transaction import apply_transaction,finish_transaction,save_journal,sync_directory
from agents.test_generation_agent import generate_spec,render,validate_spec,CASES


def scope_hash(state):
    return digest({'scope':state['scope'],'old_hash':sha(state['old_source']),
        'new_hash':sha(state['new_source']),'design_hash':state['context']['approved_design_hash'],
        'requirements_hash':state['context']['requirements_hash']})


def approval(state):
    assert_design_current(state['context'],approved=True)
    decision=interrupt({'type':'TEST_STAGE_APPROVAL','scope':state['scope'],'scope_hash':scope_hash(state),
                        'message':'Approve exact Clock amendment, additive test creation, and controlled negative fixture only.'})
    approved=decision=='approve'
    log_event(state['context']['workflow_id'],'test_stage','TEST_SCOPE_APPROVED' if approved else 'TEST_SCOPE_REJECTED',
              {'scope_hash':scope_hash(state),'scope':state['scope']})
    return {**state,'approved_scope_hash':scope_hash(state) if approved else '',
            'status':'TEST_SCOPE_APPROVED' if approved else 'STOPPED_TEST_SCOPE_REJECTED'}


def maven(workspace,evidence,label,cache):
    output=Path(evidence)/label;output.mkdir()
    env=os.environ.copy()
    for key in ('MAVEN_ARGS','MAVEN_OPTS','JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS'):env.pop(key,None)
    command=['./mvnw','-o',f'-Dmaven.repo.local={Path(cache).resolve()}','clean','test']
    result=subprocess.run(command,cwd=Path(workspace)/'url-shortener',env=env,capture_output=True,text=True,timeout=180)
    (output/'maven.log').write_text(result.stdout+'\n'+result.stderr)
    cases={}
    for report in (Path(workspace)/'url-shortener/target/surefire-reports').glob('TEST-*.xml'):
        shutil.copyfile(report,output/report.name)
        for case in ET.parse(report).getroot().iter('testcase'):
            key=case.attrib['classname']+'#'+case.attrib['name']
            if key in cases:raise PermissionError('Duplicate test identities')
            cases[key]='failure' if case.find('failure') is not None else 'error' if case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
    record={'exit_code':result.returncode,'cases':cases,'command':command}
    (output/'result.json').write_text(json.dumps(record,indent=2));return record


def require_results(record,original,negative=False):
    generated={'url_shortener.GeneratedExpirationBoundaryTest#'+name for name in
               ('futureExpiration','pastExpiration','equalNowExpiration','missingExpiration')}
    if not original or any(status!='passed' for status in original.values()):raise PermissionError('Original baseline must execute and pass without skips')
    cases=record['cases']
    if set(cases)!=set(original)|generated:raise PermissionError('Original or generated tests missing/unexpected')
    failure='url_shortener.GeneratedExpirationBoundaryTest#equalNowExpiration'
    for case,status in cases.items():
        expected='failure' if negative and case==failure else 'passed'
        if status!=expected:raise PermissionError(f'Unexpected test outcome {case}: {status}')
    if (negative and record['exit_code']==0) or (not negative and record['exit_code']!=0):raise PermissionError('Maven exit status contradicts required evidence')


def execute(state,model):
    context=state['context'];wid=context['workflow_id'];workspace=Path(context['workspace'])
    evidence=Path(state['evidence']);scope=state['scope'];source=scope['production_file'];test=scope['new_test_file']
    transaction=None;created=False;test_bytes=None
    def event(name,details):log_event(wid,'test_stage',name,details)
    try:
        assert_design_current(context,approved=True)
        if state.get('approved_scope_hash')!=scope_hash(state):raise PermissionError('Reviewed testing architecture/scope approval required')
        if scope.get('policy') is None or scope.get('generator') is None:raise PermissionError('Explicit preservation/generation policy required')
        validate_spec({'cases':[{'obligation_id':k,'spec':v} for k,v in CASES.items()]},context['obligation_contract'])
        target=new_test_target(workspace,test)
        original_inventory=inventory(workspace)
        if original_inventory[source]!=state['old_source']:raise PermissionError('Reviewed production baseline changed')
        baseline_hash=preserve(workspace,evidence/'originals')
        baseline=load_baseline(evidence/'originals',baseline_hash)
        event('TEST_STAGE_ENTRY_PASSED',{'scope_hash':scope_hash(state),'baseline_hash':baseline_hash,
               'requirements_hash':context['requirements_hash'],'design_hash':context['approved_design_hash'],
               'contract_hash':context.get('approved_contract_hash'), 'parent_workflow_id':context.get('parent_workflow_id')})
        original=maven(workspace,evidence,'baseline',state['cache'])
        if original['exit_code'] or not original['cases'] or any(v!='passed' for v in original['cases'].values()):raise PermissionError('Baseline Maven tests must run and pass')
        assert_integrity(workspace,load_baseline(evidence/'originals',baseline_hash))
        spec,prompt,raw=generate_spec(model,context,scope)
        (evidence/'generation-prompt.txt').write_text(prompt);(evidence/'generation-response.json').write_text(raw)
        test_bytes=render(spec,context['obligation_contract'])
        # Recheck every protected file before the FIRST source write.
        assert_integrity(workspace,load_baseline(evidence/'originals',baseline_hash))
        new_test_target(workspace,test)
        from governance.policy_engine import enforce
        enforce({test:test_bytes},{test:None},workspace,wid,allow_new_test_files=[test])
        journal={'status':'prepared','test_path':test,'test_hash':sha(test_bytes),'scope_hash':scope_hash(state),'baseline_hash':baseline_hash}
        save_journal(evidence/'test-journal.json',journal)
        transaction=apply_transaction({source:state['new_source']},{source:state['old_source']},workspace,[source],wid)
        journal['source_transaction_id']=transaction['transaction_id'];save_journal(evidence/'test-journal.json',journal)
        # Atomic no-overwrite publication; models cannot supply arbitrary Java.
        fd,name=tempfile.mkstemp(prefix='.test-stage-',dir=target.parent)
        temporary=Path(name)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(test_bytes);stream.flush();os.fsync(stream.fileno())
            os.link(temporary,target);created=True;sync_directory(target.parent)
        finally:temporary.unlink(missing_ok=True)
        journal['status']='applied';save_journal(evidence/'test-journal.json',journal)
        changes={source:state['new_source'],test:test_bytes}
        assert_integrity(workspace,load_baseline(evidence/'originals',baseline_hash),changes)
        event('TEST_FILE_CREATED',{'path':test,'sha256':sha(test_bytes),'scope_hash':scope_hash(state),'source_transaction':transaction['transaction_id']})
        # Explicit controlled mutation in a SEPARATE project snapshot.
        negative=evidence/'negative-workspace';negative.mkdir()
        shutil.copytree(workspace/'url-shortener',negative/'url-shortener',ignore=shutil.ignore_patterns('target','.git'))
        correct=state['new_source'];needle=b'!expiresAt.isAfter(java.time.Instant.now(clock))'
        if correct.count(needle)!=1:raise PermissionError('Controlled equality mutation anchor unavailable')
        mutant=correct.replace(needle,b'expiresAt.isBefore(java.time.Instant.now(clock))')
        mutation=apply_transaction({source:mutant},{source:correct},negative,[source],wid)
        try:
            assert_integrity(negative,load_baseline(evidence/'originals',baseline_hash),{source:mutant,test:test_bytes})
            bad=maven(negative,evidence,'negative',state['cache'])
            assert_integrity(negative,load_baseline(evidence/'originals',baseline_hash),{source:mutant,test:test_bytes})
            require_results(bad,original['cases'],negative=True)
            event('TEST_NEGATIVE_FIXTURE_CONFIRMED',{'mutation':'allow-equality','validation':bad})
        finally:finish_transaction(mutation['transaction_id'],negative,[source],passed=False)
        assert (negative/source).read_bytes()==correct
        assert_integrity(negative,load_baseline(evidence/'originals',baseline_hash),changes)
        assert_integrity(workspace,load_baseline(evidence/'originals',baseline_hash),changes)
        good=maven(workspace,evidence,'positive',state['cache']);require_results(good,original['cases'])
        assert_integrity(workspace,load_baseline(evidence/'originals',baseline_hash),changes)
        finish_transaction(transaction['transaction_id'],workspace,[source],passed=True)
        journal['status']='validated';save_journal(evidence/'test-journal.json',journal)
        event('TEST_STAGE_EXIT_PASSED',{'baseline_hash':baseline_hash,'test_hash':sha(test_bytes),'validation':good,
                                      'negative_assertion_verified':True,'scope_hash':scope_hash(state)})
        return {**state,'status':'AWAITING_TEST_RESULTS_REVIEW','validation':good,'negative_validation':bad,
                'baseline_hash':baseline_hash,'test_hash':sha(test_bytes),'source_transaction':transaction['transaction_id']}
    except Exception as error:
        recovery='no_source_writes'
        try:
            if transaction:
                if created and (not target.is_file() or target.read_bytes()!=test_bytes):raise PermissionError('Generated test changed externally; review required')
                finish_transaction(transaction['transaction_id'],workspace,[source],passed=False)
                if created:target.unlink();sync_directory(target.parent)
                recovery='rolled_back'
                if (evidence/'test-journal.json').exists():
                    journal['status']='rolled_back';save_journal(evidence/'test-journal.json',journal)
        except Exception as recovery_error:recovery='blocked: '+str(recovery_error)
        event('TEST_STAGE_REJECTED',{'error':str(error),'recovery':recovery})
        return {**state,'status':'STOPPED_TEST_STAGE','error':str(error),'recovery':recovery}


def build_test_workflow(model):
    graph=StateGraph(dict)
    graph.add_node('test_scope_review',approval)
    graph.add_node('test_execution',lambda state:execute(state,model))
    graph.add_edge(START,'test_scope_review')
    graph.add_conditional_edges('test_scope_review',lambda state:'execute' if state['status']=='TEST_SCOPE_APPROVED' else 'stop',
                                {'execute':'test_execution','stop':END})
    graph.add_edge('test_execution',END)
    return graph
