"""Prospective engineering-plan authorization, execution and test linkage.

Receipts are local audit/checkpoint records, not signatures against a privileged
administrator. The first adapter supports one implementation task and at most one
additive test file; it never upgrades historical scope approval into plan approval.
"""
import json
import sqlite3
import uuid
from pathlib import Path
from governance import audit_logger
from governance.audit_logger import log_event
from governance.requirements import assert_plan_current,assert_resolved
from governance.task_orchestrator import tasks_from_plan,task_definition_hash
from governance.semantic_plan import digest,assert_contract_approved,validate_coverage
from governance.design import design_binding
from governance.source_policy import authorized_java_target
from governance.test_safety import inventory,new_test_target,sha
from governance.file_transaction import ensure_workspace_ready


def identity(state):
    assert_resolved(state)
    assert_contract_approved(state)
    if (not state.get('design_valid') or state.get('design_hash')!=design_binding(state)
        or state.get('approved_design_hash')!=state.get('design_hash')
        or state.get('plan_design_hash')!=state.get('design_hash')
        or state.get('plan_requirements_hash')!=state.get('requirements_hash')
        or state.get('approved_requirements_hash')!=state.get('requirements_hash')
        or state.get('plan_requirement_version')!=state.get('requirement_version')
        or not state.get('semantic_valid') or state.get('semantic_plan_hash')!=digest(state['plan'])):
        raise PermissionError('Requirements, design or semantic-plan approval binding is stale')
    if state.get('approval')!='approve' or not state.get('design_required'):
        raise PermissionError('Explicit engineering-plan and design approval required')
    tasks=tasks_from_plan(state['plan'],'',strict=True)
    if task_definition_hash(tasks)!=state.get('approved_task_hash') or task_definition_hash(state['tasks'])!=state['approved_task_hash']:
        raise PermissionError('Approved task definitions changed')
    if len([t for t in tasks if t['execution_type']=='implementation'])!=1:
        raise PermissionError('Lineage v1 supports exactly one implementation task')
    validate_coverage(state['plan'],tasks,state['obligation_contract'])
    root=Path(state['workspace']).resolve(strict=True)
    return {'workflow_id':state['workflow_id'],'workspace':str(root),
            'requirements_hash':state['requirements_hash'],'requirement_version':state['requirement_version'],
            'design_hash':state['approved_design_hash'],'contract_hash':state['approved_contract_hash'],
            'plan_hash':digest(state['plan']),'task_hash':state['approved_task_hash']},tasks


def events(state):
    with sqlite3.connect(f"file:{Path(state['plan_authorization_audit_db']).resolve()}?mode=ro",uri=True) as conn:
        return [{'id':i,'event':e,'details':json.loads(d)} for i,e,d in conn.execute(
            'SELECT id,event,details FROM audit_events WHERE workflow_id=? ORDER BY id',(state['workflow_id'],))]


def receipt(state,event,value):
    for entry in events(state):
        if entry['event']==event and entry['details']==value:return entry['id']
    raise PermissionError(f'Missing exact {event} audit receipt')


def authorize(state):
    """Called only by the human plan-review node AFTER its explicit approval."""
    assert_plan_current(state,approved=True)
    body,tasks=identity(state);root=Path(body['workspace']);ensure_workspace_ready(root)
    all_files={p:sha(data) for p,data in inventory(root).items()}
    test_stage=json.loads(state['plan']).get('test_stage',{'allowed_new_test_files':[], 'protected_existing_test_files':[]})
    if not isinstance(test_stage,dict) or set(test_stage)!={'allowed_new_test_files','protected_existing_test_files'}:
        raise PermissionError('Explicit additive/protected test policy required')
    protected=test_stage['protected_existing_test_files']
    if not isinstance(protected,list) or not all(isinstance(p,str) for p in protected) or len(set(protected))!=len(protected):
        raise PermissionError('Invalid protected-test policy')
    for path in protected:
        if not path.startswith('url-shortener/src/test/java/') or path not in all_files:
            raise PermissionError('Only existing tests may be declared read-only')
    for task in tasks:
        for path in task['allowed_files']:
            authorized_java_target(path,root,task['allowed_files'])
            if task['execution_type']=='implementation' and not path.startswith('url-shortener/src/main/java/') and path not in protected:
                raise PermissionError('Original test references must be explicitly protected, never edited')
    additions=test_stage['allowed_new_test_files']
    if not isinstance(additions,list) or len(additions)>1:raise PermissionError('Lineage v1 supports at most one new test file')
    for path in additions:new_test_target(root,path)
    body.update({'project_baseline':all_files,'test_additions':additions,'protected_existing_test_files':protected})
    result={'body':body,'authorization_hash':digest(body)}
    log_event(state['workflow_id'],'plan_lineage','ENGINEERING_PLAN_AUTHORIZED',result)
    return {'plan_authorization':result,'plan_authorization_audit_db':str(Path(audit_logger.DB_PATH).resolve())}


def approved(state):
    auth=state.get('plan_authorization')
    if not isinstance(auth,dict) or set(auth)!={'body','authorization_hash'} or digest(auth['body'])!=auth['authorization_hash']:
        raise PermissionError('Engineering-plan authorization missing or tampered')
    expected,_=identity(state)
    if any(auth['body'].get(k)!=v for k,v in expected.items()):raise PermissionError('Plan authorization identity mismatch')
    receipt(state,'ENGINEERING_PLAN_AUTHORIZED',auth)
    return auth


def before_execution(state,task):
    assert_plan_current(state,approved=True)
    auth=approved(state);root=Path(state['workspace']).resolve()
    if task.get('execution_type')!='implementation' or task not in state['tasks']:
        raise PermissionError('Task is not the authorized implementation')
    actual={p:sha(data) for p,data in inventory(root).items()}
    if actual!=auth['body']['project_baseline']:raise PermissionError('Workspace changed after plan approval')
    return auth


def source_journal(root,identifier):
    if str(uuid.UUID(identifier))!=identifier:raise PermissionError('Invalid source transaction ID')
    path=Path(root)/'.agent-transactions'/(identifier+'.json')
    if path.is_symlink():raise PermissionError('Source journal cannot be a symlink')
    return json.loads(path.read_text())


def bind_execution(state,task,transaction):
    auth=approved(state);root=Path(state['workspace']).resolve()
    if task.get('execution_type')!='implementation' or task not in state['tasks']:
        raise PermissionError('Task is not the authorized implementation')
    auth_id=receipt(state,'ENGINEERING_PLAN_AUTHORIZED',auth)
    journal=source_journal(root,transaction['transaction_id'])
    if journal['workflow_id']!=state['workflow_id'] or journal['status']!='applied':raise PermissionError('Wrong or unapplied source transaction')
    paths={e['path'] for e in journal['files']}
    if paths!=set(task['allowed_files'])-set(auth['body']['protected_existing_test_files']):raise PermissionError('Transaction scope differs from approved task')
    expected=dict(auth['body']['project_baseline'])
    for entry in journal['files']:
        if entry['original_hash']!=expected[entry['path']]:raise PermissionError('Transaction did not start from approved baseline')
        expected[entry['path']]=entry['expected_current_hash']
    if {p:sha(v) for p,v in inventory(root).items()}!=expected:raise PermissionError('Unapproved changes during execution')
    applied=[e for e in events(state) if e['event']=='TRANSACTION_APPLIED' and e['details'].get('transaction_id')==transaction['transaction_id'] and e['id']>auth_id]
    if not applied:raise PermissionError('Approval must precede application; historical edits cannot be adopted')
    body={'authorization_hash':auth['authorization_hash'],'transaction_id':transaction['transaction_id'],
          'task_id':task['task_id'],'workspace':str(root),'expected_project_hashes':expected}
    result={'body':body,'execution_hash':digest(body)}
    log_event(state['workflow_id'],'plan_lineage','ENGINEERING_EXECUTION_BOUND',result)
    return result


def _check_test_link(state,execution,test_path,test_hash,scope_hash,validation):
    auth=approved(state);body=execution['body']
    if execution.get('execution_hash')!=digest(body) or body['authorization_hash']!=auth['authorization_hash']:
        raise PermissionError('Execution receipt identity/hash mismatch')
    execution_id=receipt(state,'ENGINEERING_EXECUTION_BOUND',execution)
    if execution_id<=receipt(state,'ENGINEERING_PLAN_AUTHORIZED',auth):
        raise PermissionError('Execution binding must follow plan approval')
    if body['workspace']!=str(Path(state['workspace']).resolve()):raise PermissionError('Foreign workspace cannot reuse execution')
    if auth['body']['test_additions']!=[test_path]:raise PermissionError('Test creation was not included in approved plan')
    if (not isinstance(validation,dict) or validation.get('exit_code')!=0 or not validation.get('cases')
        or any(value!='passed' for value in validation['cases'].values())):
        raise PermissionError('Only passing, executed, unskipped validation may be linked')
    expected={**body['expected_project_hashes'],test_path:test_hash}
    if {p:sha(v) for p,v in inventory(state['workspace']).items()}!=expected:raise PermissionError('Test/source/build integrity mismatch')
    journal=source_journal(state['workspace'],body['transaction_id'])
    if journal['status']!='validated' or journal['workflow_id']!=state['workflow_id']:
        raise PermissionError('Implementation transaction has not passed matching workflow validation')
    tasks=state['tasks']
    task=next((t for t in tasks if t['task_id']==body['task_id']),None)
    if task is None or task.get('execution_type')!='implementation':
        raise PermissionError('Execution task is not approved')
    if {e['path'] for e in journal['files']}!=set(task['allowed_files'])-set(auth['body']['protected_existing_test_files']):
        raise PermissionError('Validated transaction scope mismatch')
    journal_expected=dict(auth['body']['project_baseline'])
    for entry in journal['files']:
        if entry['original_hash']!=journal_expected[entry['path']]:
            raise PermissionError('Validated transaction baseline mismatch')
        journal_expected[entry['path']]=entry['expected_current_hash']
    if journal_expected!=body['expected_project_hashes']:
        raise PermissionError('Validated transaction differs from execution receipt')
    rows=events(state)
    scope=[e for e in rows if e['event']=='TEST_SCOPE_APPROVED' and e['details'].get('scope_hash')==scope_hash]
    creates=[e for e in rows if e['event']=='TEST_FILE_CREATED' and e['id']>execution_id and e['details'].get('path')==test_path
             and e['details'].get('sha256')==test_hash and e['details'].get('source_transaction')==body['transaction_id']]
    exits=[e for e in rows if e['event']=='TEST_STAGE_EXIT_PASSED' and e['details'].get('test_hash')==test_hash
           and e['details'].get('scope_hash')==scope_hash and e['details'].get('validation')==validation]
    if not scope or not creates or not exits or not any(s['id']<c['id']<x['id'] for s in scope for c in creates for x in exits):
        raise PermissionError('Matching ordered test approval, creation and validation audit chain required')
    result={'authorization_hash':auth['authorization_hash'],'execution_hash':execution['execution_hash'],
            'plan_hash':auth['body']['plan_hash'],'workspace':body['workspace'],'test_path':test_path,'test_hash':test_hash,
            'validation_hash':digest(validation),'scope_hash':scope_hash}
    result['lineage_hash']=digest(result)
    return result


def link_tests(state,execution,test_path,test_hash,scope_hash,validation):
    result=_check_test_link(state,execution,test_path,test_hash,scope_hash,validation)
    log_event(state['workflow_id'],'plan_lineage','ENGINEERING_TESTS_BOUND',result)
    return result


def verify_test_link(state,execution,link,source_transaction,test_path,test_hash,scope_hash,validation):
    # Re-run linkage preconditions, without minting a new approval or audit event.
    expected={k:v for k,v in link.items() if k!='lineage_hash'}
    if digest(expected)!=link.get('lineage_hash'):raise PermissionError('Test linkage hash mismatch')
    receipt(state,'ENGINEERING_TESTS_BOUND',link)
    if source_transaction!=execution['body']['transaction_id']:raise PermissionError('Validation belongs to another source transaction')
    checked=_check_test_link(state,execution,test_path,test_hash,scope_hash,validation)
    if checked!=link:raise PermissionError('Evidence belongs to another plan/execution/test scope')
    return checked['plan_hash']
