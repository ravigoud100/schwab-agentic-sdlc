"""Evidence-bound review -> documentation -> independent readiness -> final review.

This continuation never deploys, merges, grants implementation approval, or treats
an approved test scope as an approved task plan.
"""
from pathlib import Path
import json
import sqlite3
from langgraph.graph import StateGraph,START,END
from langgraph.types import interrupt
from governance.audit_logger import log_event
from governance import audit_logger
from governance.release_evidence import verify,facts
from governance.semantic_plan import digest
from governance.test_safety import sha
from agents.documentation_agent import generate,validate_selection


def log(state,event,details):log_event(state['run_id'],'documentation_release',event,details)


def current(state):
    manifest=verify(state['origin'])
    if digest(manifest)!=state['bundle_hash']:raise PermissionError('Engineering evidence changed after bundle review')
    return manifest


def audit_approved(state,event,document_hash=None):
    try:
        with sqlite3.connect(f'file:{Path(audit_logger.DB_PATH).resolve()}?mode=ro',uri=True) as conn:
            rows=conn.execute('SELECT details FROM audit_events WHERE workflow_id=? AND event=?',
                              (state['run_id'],event)).fetchall()
        return any((details:=json.loads(row[0])).get('bundle_hash')==state['bundle_hash']
                   and (document_hash is None or details.get('sha256')==document_hash) for row in rows)
    except (OSError,sqlite3.Error,ValueError):return False


def review_tests(state):
    try:manifest=current(state)
    except Exception as error:
        log(state,'TEST_RESULTS_REVIEW_BLOCKED',{'error':str(error)})
        return {**state,'status':'STOPPED_EVIDENCE_INVALID','error':str(error)}
    decision=interrupt({'type':'TEST_RESULTS_REVIEW','bundle_hash':state['bundle_hash'],
        'workspace':manifest['workspace'],'implementation_hash':manifest['implementation_hash'],
        'generated_test_hash':manifest['generated_test_hash'],'validation':manifest['records'],
        'message':'Review these exact results. This does not approve an engineering plan or release.'})
    approved=decision=='approve'
    log(state,'TEST_RESULTS_APPROVED' if approved else 'TEST_RESULTS_REJECTED',{'bundle_hash':state['bundle_hash']})
    return {**state,'test_review_hash':state['bundle_hash'] if approved else '',
            'status':'TEST_RESULTS_APPROVED' if approved else 'STOPPED_TEST_RESULTS_REJECTED'}


def documentation(state,model):
    try:
        manifest=current(state)
        if state.get('test_review_hash')!=state['bundle_hash'] or not audit_approved(state,'TEST_RESULTS_APPROVED'):
            raise PermissionError('Hash-bound human test review audit receipt missing')
        directory=Path(state['output_dir']).resolve(strict=True)
        if directory.is_relative_to(Path(manifest['workspace'])):raise PermissionError('Documentation must be a separate additive artifact')
        path=directory/'engineering-notes.md'
        if path.exists() or path.is_symlink():raise PermissionError('Existing documentation cannot be overwritten')
        content,payload,prompt,raw=generate(model,manifest,state.get('documentation_errors',[]))
        current(state)  # detect changed sources, logs or reports during generation
        # These are new run artifacts; no model-supplied paths or free prose.
        for name,data in [('documentation-prompt.txt',prompt.encode()),('documentation-response.json',raw.encode()),('engineering-notes.md',content)]:
            with (directory/name).open('xb') as stream:stream.write(data)
        document={'path':str(path),'sha256':sha(content),'selection':payload,'bundle_hash':state['bundle_hash']}
        log(state,'DOCUMENTATION_GATE_PASSED',document)
        return {**state,'document':document,'status':'DOCUMENTATION_VALIDATED','error':'','documentation_errors':[]}
    except Exception as error:
        log(state,'DOCUMENTATION_GATE_REJECTED',{'error':str(error),'bundle_hash':state['bundle_hash']})
        attempt=state.get('documentation_attempts',0)+1
        if hasattr(error,'raw_response'):
            for suffix,value in [('prompt.txt',error.prompt),('response.txt',error.raw_response)]:
                with (directory/f'rejected-documentation-{attempt}-{suffix}').open('xb') as stream:
                    stream.write(value.encode())
        # Existing output or invalid lineage is not a regenerable model problem.
        retry=isinstance(error,(ValueError,TypeError,KeyError)) and attempt<3
        return {**state,'document':{},'status':'DOCUMENTATION_RETRY' if retry else 'STOPPED_DOCUMENTATION_INVALID',
                'error':str(error),'documentation_attempts':attempt,'documentation_errors':[str(error)]}


def evaluate(state):
    checks={};errors=[]
    try:
        manifest=current(state)
        checks.update({'artifact_lineage_and_source_integrity':True,'requirements_and_design_approved':True,
                       ('test_generation_scope_approved' if manifest.get('profile')=='integrated-expiration-v1' else 'clock_and_test_scope_approved'):True,'original_and_generated_tests_verified':True,
                       'genuine_maven_evidence':True})
        checks['matching_implementation_plan_approved']=bool(manifest.get('approved_implementation_plan_hash'))
    except Exception as error:
        manifest=None;checks['artifact_lineage_and_source_integrity']=False;errors.append(str(error))
    checks['human_test_results_review']=(state.get('test_review_hash')==state['bundle_hash']
                                        and audit_approved(state,'TEST_RESULTS_APPROVED'))
    try:
        if manifest is None:raise PermissionError('Evidence invalid')
        document=state['document']
        validate_selection(document['selection'],facts(manifest))
        # Regenerate exact renderer output without another LLM judgment.
        expected='# Verified engineering documentation\n\n'
        catalog=facts(manifest)
        for section,ids in document['selection'].items():
            expected+='## '+section.replace('_',' ').title()+'\n\n'
            expected+='\n\n'.join(catalog[section][i] for i in ids)+'\n\n'
        actual=Path(document['path']).read_bytes()
        if Path(document['path']).resolve()!=Path(state['output_dir']).resolve()/'engineering-notes.md':
            raise PermissionError('Documentation output path is not authorized')
        if document['bundle_hash']!=state['bundle_hash'] or sha(actual)!=document['sha256'] or actual!=expected.encode():
            raise PermissionError('Documentation claims/hash/lineage mismatch')
        if not audit_approved(state,'DOCUMENTATION_GATE_PASSED',document['sha256']):
            raise PermissionError('Documentation gate audit receipt missing')
        checks['documentation_verified']=True
    except Exception as error:checks['documentation_verified']=False;errors.append(str(error))
    try:
        if manifest is None:raise PermissionError('Verified engineering manifest required for policy evaluation')
        from governance.policy_engine import release_controls
        technical=release_controls(state,manifest)
        checks.update({k:v for k,v in technical.items() if isinstance(v,bool)})
        policy_evidence={k:v for k,v in technical.items() if not isinstance(v,bool)}
    except Exception as error:
        checks.update(static_security_compliance_controls=False,protected_inventory_and_tests=False,
                      operational_validation_evidence=False)
        policy_evidence={};errors.append(str(error))
    try:
        from governance.audit_logger import verify_audit_integrity_readonly
        audit_valid, audit_report = verify_audit_integrity_readonly(
            Path(audit_logger.DB_PATH),
            workflow_id=state.get('run_id') or state.get('workflow_id'),
        )
        checks['audit_log_integrity_verified'] = audit_valid
        if not audit_valid:
            errors.append(f"Audit log integrity check failed: {audit_report.get('error', 'Integrity verification failure')}")
    except Exception as error:
        checks['audit_log_integrity_verified'] = False
        errors.append(f"Audit log integrity check failed: {str(error)}")
    # Technical controls do not create organizational human sign-offs.
    checks['human_security_signoff']=False
    checks['human_compliance_signoff']=False
    checks['human_operational_signoff']=False
    # Production operational/security and release authorization are separate from Maven.
    checks['release_security_and_operational_signoff']=False
    report={'status':'READY_FOR_FINAL_REVIEW' if all(checks.values()) else 'BLOCKED',
        'bundle_hash':state['bundle_hash'],'checks':checks,
        'blockers':[key for key,value in checks.items() if not value],'errors':errors,
        'policy_evidence':policy_evidence,
        'deployment_authorized':False,'earlier_four_file_plan_authorized':False}
    log(state,'RELEASE_READINESS_EVALUATED',report)
    return {**state,'readiness':report,'status':'AWAITING_FINAL_HUMAN_REVIEW'}


def final_review(state):
    decision=interrupt({'type':'FINAL_RELEASE_REVIEW','report':state['readiness'],
                        'message':'Blocked readiness cannot be overridden by this review. No deployment is performed.'})
    log(state,'FINAL_RELEASE_REVIEW_RECORDED',{'decision':decision,'report_hash':digest(state['readiness'])})
    return {**state,'status':'STOPPED_RELEASE_BLOCKED' if state['readiness']['status']=='BLOCKED' else 'FINAL_REVIEW_RECORDED'}


def build_workflow(model):
    graph=StateGraph(dict)
    graph.add_node('test_results_review',review_tests)
    graph.add_node('documentation',lambda state:documentation(state,model))
    graph.add_node('readiness',evaluate)
    graph.add_node('final_review',final_review)
    graph.add_edge(START,'test_results_review')
    graph.add_conditional_edges('test_results_review',lambda s:'docs' if s['status']=='TEST_RESULTS_APPROVED' else 'stop',{'docs':'documentation','stop':END})
    graph.add_conditional_edges('documentation',lambda s:'evaluate' if s['status']=='DOCUMENTATION_VALIDATED'
        else 'retry' if s['status']=='DOCUMENTATION_RETRY' else 'stop',{'evaluate':'readiness','retry':'documentation','stop':END})
    graph.add_edge('readiness','final_review');graph.add_edge('final_review',END)
    return graph
