"""Verify test-stage evidence against its checkpoint and workspace, never loose exports."""
import json
import difflib
from pathlib import Path
import xml.etree.ElementTree as ET
import sqlite3
from governance.design import assert_design_current
from governance.test_stage import scope_hash,require_results
from governance.test_safety import load_baseline,assert_integrity,sha
from governance.semantic_plan import digest
from governance.semantic_plan import custom_expiration_contract
from agents.test_generation_agent import render,CASES,validate_spec


def verify(state):
    profile = state.get('profile', '')
    if profile.startswith('integrated-'):
        from governance.downstream_sdlc import verify_integrated
        return verify_integrated(state)
    if profile.startswith('parallel-'):
        from governance.downstream_sdlc import verify_parallel
        return verify_parallel(state)
    context=state['context'];workspace=Path(context['workspace']).resolve(strict=True)
    evidence=Path(state['evidence']).resolve(strict=True)
    assert_design_current(context,approved=True)
    if context['obligation_contract']!=custom_expiration_contract():raise PermissionError('No vetted documentation profile for this contract')
    if state.get('status')!='AWAITING_TEST_RESULTS_REVIEW':raise PermissionError('Test stage did not pass')
    if state.get('approved_scope_hash')!=scope_hash(state):raise PermissionError('Testing scope/Clock amendment not approved')
    baseline=load_baseline(evidence/'originals',state['baseline_hash'])
    source=state['scope']['production_file'];test=state['scope']['new_test_file']
    generated=(workspace/test).read_bytes()
    expected_test=render({'cases':[{'obligation_id':k,'spec':v} for k,v in CASES.items()]},context['obligation_contract'])
    if generated!=expected_test:raise PermissionError('Test source differs from vetted assertion profile')
    generated_spec=json.loads((evidence/'generation-response.json').read_text())
    validate_spec(generated_spec,context['obligation_contract'])
    if render(generated_spec,context['obligation_contract'])!=generated:raise PermissionError('Test specification/source mismatch')
    if sha(state['new_source'])!='3f1a3ef024c57d61b712cdc7d21ffadc5c8857143564643d5b9beab4106463a2':
        raise PermissionError('No verified source-fact profile for this implementation')
    if sha(baseline['url-shortener/src/test/java/url_shortener/UrlExpirationIntegrationTests.java'])!='43a670a89b5a762156a0a7b4b0aab3561867a569c5c8e748808410780be66ff2':
        raise PermissionError('Unknown original HTTP assertion profile')
    if sha(generated)!=state['test_hash']:raise PermissionError('Generated test hash changed')
    assert_integrity(workspace,baseline,{source:state['new_source'],test:generated})
    journal=json.loads((evidence/'test-journal.json').read_text())
    if (journal['status']!='validated' or journal['scope_hash']!=scope_hash(state)
        or journal['test_hash']!=state['test_hash'] or journal['baseline_hash']!=state['baseline_hash']
        or journal['source_transaction_id']!=state['source_transaction']):raise PermissionError('Test journal lineage mismatch')
    source_journal=workspace/'.agent-transactions'/(state['source_transaction']+'.json')
    record=json.loads(source_journal.read_text())
    if record['status']!='validated' or len(record['files'])!=1 or record['workflow_id']!=context['workflow_id']:
        raise PermissionError('Source transaction not validated for this workflow')
    item=record['files'][0]
    if item['path']!=source or item['original_hash']!=sha(state['old_source']) or item['expected_current_hash']!=sha(state['new_source']):
        raise PermissionError('Source transaction does not match approved implementation')
    records={};artifact_hashes={}
    for phase in ('baseline','negative','positive'):
        path=evidence/phase/'result.json';result=json.loads(path.read_text());cases={}
        reports=sorted((evidence/phase).glob('TEST-*.xml'))
        if not reports:raise PermissionError('Genuine Surefire reports required')
        for report in reports:
            artifact_hashes[str(report)]=sha(report.read_bytes())
            for case in ET.parse(report).getroot().iter('testcase'):
                key=case.attrib['classname']+'#'+case.attrib['name']
                if key in cases:raise PermissionError('Duplicate Surefire identity')
                cases[key]='failure' if case.find('failure') is not None else 'error' if case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
        if cases!=result['cases']:raise PermissionError('Maven summary disagrees with original Surefire XML')
        command=result['command']
        if len(command)!=5 or command[0:2]!=['./mvnw','-o'] or not command[2].startswith('-Dmaven.repo.local=') or command[3:]!=['clean','test']:
            raise PermissionError('Unexpected validation command or test exclusions')
        log=evidence/phase/'maven.log'
        if not log.is_file() or not log.stat().st_size:raise PermissionError('Missing Maven execution log')
        artifact_hashes[str(log)]=sha(log.read_bytes());artifact_hashes[str(path)]=sha(path.read_bytes())
        records[phase]=result
    if records['positive']!=state['validation'] or records['negative']!=state['negative_validation']:
        raise PermissionError('Validation evidence differs from checkpoint')
    if records['baseline']['exit_code']!=0:raise PermissionError('Baseline Maven execution did not pass')
    require_results(records['positive'],records['baseline']['cases'])
    require_results(records['negative'],records['baseline']['cases'],negative=True)
    for path in (source_journal,evidence/'test-journal.json',evidence/'generation-prompt.txt',evidence/'generation-response.json'):
        artifact_hashes[str(path)]=sha(path.read_bytes())
    with sqlite3.connect(f"file:{Path(state['origin_audit_db']).resolve()}?mode=ro",uri=True) as conn:
        events=[{'event':name,'details':json.loads(details)} for name,details in conn.execute(
            'SELECT event,details FROM audit_events WHERE workflow_id=? ORDER BY id',(context['workflow_id'],))]
    scope_events=[e for e in events if e['event']=='TEST_SCOPE_APPROVED' and e['details'].get('scope_hash')==state['approved_scope_hash']]
    exits=[e for e in events if e['event']=='TEST_STAGE_EXIT_PASSED' and e['details'].get('validation')==state['validation']
           and e['details'].get('scope_hash')==state['approved_scope_hash']
           and e['details'].get('baseline_hash')==state['baseline_hash'] and e['details'].get('test_hash')==state['test_hash']]
    if not scope_events or not exits:raise PermissionError('Required testing approval/exit audit evidence missing')
    plan_hash=None
    if state.get('engineering_test_link'):
        from governance.plan_lineage import verify_test_link
        execution=context.get('implementation_transaction',{}).get('execution_receipt')
        if not execution:raise PermissionError('Test link has no engineering execution receipt')
        plan_hash=verify_test_link(context,execution,state['engineering_test_link'],state['source_transaction'],
                                  test,state['test_hash'],state['approved_scope_hash'],state['validation'])
    return {'workflow_id':context['workflow_id'],'workspace':str(workspace),
        'requirements_hash':context['requirements_hash'],'requirement_version':context['requirement_version'],
        'approved_design_hash':context['approved_design_hash'],'approved_contract_hash':context['approved_contract_hash'],
        'approved_clock_test_scope_hash':state['approved_scope_hash'],'original_baseline_hash':state['baseline_hash'],
        'implementation_hash':sha(state['new_source']),'source_path':source,'generated_test_hash':state['test_hash'],
        'test_path':test,'source_transaction':state['source_transaction'],'records':records,'artifacts':artifact_hashes,
        'requirements':context['structured_requirements'],'clarification_answers':context['clarification_answers'],
        'approved_design':context['design_artifact'],'approved_contract':context['obligation_contract'],
        'approved_clock_amendment':state['scope'],'actual_diff':''.join(difflib.unified_diff(
            state['old_source'].decode().splitlines(True),state['new_source'].decode().splitlines(True),fromfile=source,tofile=source)),
        'audit_events':events,
        # A narrowly approved patch scope is NOT an approved engineering task plan.
        'approved_implementation_plan_hash':plan_hash}


def facts(manifest):
    contract = manifest.get('approved_contract')
    if contract:
        from governance.feature_registry import get_feature_adapter
        try:
            adapter = get_feature_adapter(contract)
            return adapter.get_facts(manifest)
        except Exception:
            pass
    if manifest.get('profile') in {'integrated-expiration-v1', 'parallel-expiration-v1'}:
        is_parallel = manifest.get('profile') == 'parallel-expiration-v1'

        source_origin = (
            'The model executed multi-task concurrent implementations under an approved DAG plan; '
            'independent transactions and actual diff identify the applied changes.'
            if is_parallel else
            'The model generated structured production edits under a prospectively approved engineering plan; '
            'the transaction and actual diff identify the applied changes.'
        )
        bounded_limit = (
            'This parallel path supports concurrent implementation tasks, multi-wave validation, '
            'one additive expiration test, and a vetted renderer.'
            if is_parallel else
            'This sequential path supports one implementation task, one additive expiration test, and a vetted renderer. It does not establish arbitrary test synthesis or parallel execution.'
        )
        return {
          'api_change_summary':{'contract':'The approved requirement contract and design accompany this evidence bundle; declared coverage is not proof of arbitrary implementation semantics.'},
          'implementation_notes':{
            'source_origin': source_origin,
            'source_hash':f"Verified production-set SHA-256: {manifest['implementation_hash']}.",
            'test_origin':'The model supplied constrained expiration-case specifications; a trusted renderer produced the additive JUnit file.'},
          'testing_evidence':{
            'baseline':f"Original baseline: {len(manifest['records']['baseline']['cases'])} passing tests.",
            'positive':f"Combined implementation and generated tests: {len(manifest['records']['positive']['cases'])} passing tests, no skips; original tests/build files remain byte-identical."},
          'limitations':{'bounded': bounded_limit,
                         'negative':'No negative-fixture sensitivity claim is made by this integrated run.'},
          'release_considerations':{'lineage':f"Prospective engineering-plan authorization is verified for plan {manifest['approved_implementation_plan_hash']}.",
                                    'release':'Human test review is required. Security, operational and release sign-offs remain separate; no deployment or merge is authorized.'}}
    positive=manifest['records']['positive'];negative=manifest['records']['negative']
    return {
      'api_change_summary':{
        'api': 'Optional caller-provided future UTC expiration and HTTP 400 for nonfuture creation input are preserved; existing HTTP 201/404/410 and redirect behavior are covered by retained integration tests.'},
      'implementation_notes':{
        'source_origin':'Clock implementation is the exact human-reviewed, assistant-authored patch, not live AI source generation.',
        'clock':'UrlService retains its Spring constructor, delegates to Clock.systemUTC(), and adds an explicit Clock constructor used by deterministic tests.',
        'source_hash':f"Verified implementation SHA-256: {manifest['implementation_hash']}.",
        'test_origin':'The live model supplied constrained test-case JSON; a trusted deterministic renderer produced JUnit source.'},
      'testing_evidence':{
        'baseline':f"Original baseline: {len(manifest['records']['baseline']['cases'])} passing tests.",
        'positive':f"Correct implementation: {len(positive['cases'])} tests, all passed, with original tests byte-identical.",
        'negative':f"Controlled equality-allowing fixture: {len(negative['cases'])} tests; only generated equalNowExpiration failed. This was deliberately seeded, not a spontaneous model defect."},
      'limitations':{
        'bounded':'Only the reviewed expiration profile and vetted renderer are demonstrated; unrestricted test synthesis and complete SDLC integration are not established.',
        'plan':('A matching engineering task plan is verified by prospective authorization, execution and test receipts; scope approval alone was not substituted.'
                if manifest.get('approved_implementation_plan_hash') else
                'The earlier four-file engineering plan remains unapproved. The Clock/test scope does not substitute for an approved implementation task plan.')},
      'release_considerations':{
        'review':'A hash-bound human test-results decision is required. Documentation and test success alone cannot authorize release.',
        'release':'Deployment, merge, release authorization, security/compliance sign-off, and production operational readiness are not established by this demonstration.'}}
