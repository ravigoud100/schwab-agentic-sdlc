"""Declarative, fail-closed static controls. No model controls policy or approval.

This is conservative lexical analysis, not a Java semantic security proof.
Receipt authority is the local audit DB, not a signature against privileged edits.
"""
import json
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from governance import audit_logger
from governance.audit_logger import log_event
from governance.semantic_plan import digest
from governance.test_safety import sha

POLICY = Path(__file__).resolve().parents[1]/'policies/security-v1.json'
execution_context = ContextVar('policy_execution_context', default=None)


@contextmanager
def workflow_policy_context(workflow_id, approvals=()):
    token=execution_context.set((workflow_id, approvals))
    try:yield
    finally:execution_context.reset(token)


class PolicyViolation(PermissionError):
    def __init__(self, report):
        self.policy_report=report
        super().__init__('Policy gate: '+report['decision']+' ('+', '.join(sorted({f['policy_id'] for f in report['findings']}))+')')


class PolicyApprovalRequired(PolicyViolation): pass


def load_policy():
    policy=json.loads(POLICY.read_text())
    if (set(policy)!={'version','rules','allowed_import_prefixes','reviewable_process_argv','existing_tests','configuration'}
        or policy['version']!=1 or policy['existing_tests']!='immutable' or policy['configuration']!='immutable'):
        raise PermissionError('Unsupported policy configuration')
    seen=set()
    for rule in policy['rules']:
        if set(rule)!={'id','pattern','decision'} or rule['id'] in seen or rule['decision']!='deny':
            raise PermissionError('Unsupported or duplicate policy rule')
        seen.add(rule['id']);re.compile(rule['pattern'])
    if seen!={'SEC-SECRETS','SEC-PROCESS','SEC-SQL','COM-PII-LOG','TEST-DISABLE','GOV-OBFUSCATION'}:
        raise PermissionError('Mandatory controls missing')
    if policy['reviewable_process_argv'] != [['/usr/bin/true']]:
        raise PermissionError('Unsupported process exception profile')
    if not isinstance(policy['allowed_import_prefixes'],list) or not all(isinstance(p,str) and p.endswith('.') and p for p in policy['allowed_import_prefixes']):
        raise PermissionError('Invalid dependency allowlist')
    return policy


def evaluate(proposals, originals, workspace, workflow_id, *, phase='pre_write', allow_new_test_files=(), allow_new_source_files=()):
    policy=load_policy();findings=[]
    if not proposals or set(proposals)!=set(originals):raise PermissionError('Complete original/proposed policy scope required')
    body={'policy_hash':digest(policy),'workflow_id':workflow_id,'workspace':str(Path(workspace).resolve(strict=True)),
          'phase':phase,'files':{},'authorized_additive_tests':sorted(allow_new_test_files),
          'authorized_new_production_files':sorted(allow_new_source_files),
          'checked_policy_ids':sorted([r['id'] for r in policy['rules']]+['SEC-DEPENDENCY-ALLOWLIST','GOV-PROTECTED-CONFIG','TEST-PRESERVE-ORIGINAL','TEST-UNAUTHORIZED-CREATION','GOV-UNSUPPORTED-CREATION','GOV-UNSCANNABLE'])}
    def finding(rule,path,text='',start=0,decision='deny'):
        findings.append({'policy_id':rule,'path':path,'line':text.count('\n',0,start)+1,
                         'evidence_sha256':sha(text.encode()),'decision':decision})
    for path,data in sorted(proposals.items()):
        old=originals[path]
        if not isinstance(data,bytes) or (old is not None and not isinstance(old,bytes)):
            raise PermissionError('Policy requires byte-exact source snapshots')
        body['files'][path]={'original_hash':sha(old) if old is not None else None,'proposed_hash':sha(data)}
        rel=Path(path)
        if (rel.is_absolute() or '..' in rel.parts or rel.as_posix()!=path or rel.suffix!='.java'
                or not any(rel.is_relative_to(p) for p in ('url-shortener/src/main/java','url-shortener/src/test/java'))):
            finding('GOV-PROTECTED-CONFIG',path);continue
        if rel.is_relative_to('url-shortener/src/test/java'):
            if old is None and path not in allow_new_test_files:finding('TEST-UNAUTHORIZED-CREATION',path)
            if old is not None and old!=data:finding('TEST-PRESERVE-ORIGINAL',path)
        elif old is None and path not in allow_new_source_files:finding('GOV-UNSUPPORTED-CREATION',path)
        try:text=data.decode('utf-8')
        except UnicodeError:finding('GOV-UNSCANNABLE',path);continue
        if not text.strip():finding('GOV-UNSCANNABLE',path,text)
        for rule in policy['rules']:
            for match in re.finditer(rule['pattern'],text):
                decision='deny'
                if rule['id']=='SEC-PROCESS' and match.group().strip()=='ProcessBuilder':
                    # Only this exact constant argument list is reviewable. No
                    # variables, shell, extra args, pipelines or chained options.
                    tail=text[match.start():]
                    call=re.match(r'ProcessBuilder\s*\(\s*"/usr/bin/true"\s*\)',tail)
                    if call:decision='requires_approval'
                finding(rule['id'],path,text,match.start(),decision)
        for match in re.finditer(r'\bimport\s+(?:static\s+)?([\w.]+)(?:\.\*)?\s*;',text):
            name=match.group(1)
            if not any((name.startswith(prefix) or name==prefix.rstrip('.')) for prefix in policy['allowed_import_prefixes']):
                finding('SEC-DEPENDENCY-ALLOWLIST',path,text,match.start())
        # Reject common fully-qualified dependency construction without imports.
        for match in re.finditer(r'\b((?:[a-z_]\w*\.)+[A-Z]\w*)',text):
            if not any(match.group(1).startswith(prefix) for prefix in policy['allowed_import_prefixes']):
                finding('SEC-DEPENDENCY-ALLOWLIST',path,text,match.start())
    decision='deny' if any(f['decision']=='deny' for f in findings) else 'requires_approval' if findings else 'allow'
    return {'body':body,'findings':findings,'decision':decision,'evidence_hash':digest({'body':body,'findings':findings,'decision':decision})}


def audit_payload(report):
    return {
        'policy_hash':report['body']['policy_hash'],'evidence_hash':report['evidence_hash'],
        'policy_ids':sorted({f['policy_id'] for f in report['findings']}) or ['BASELINE-STATIC-CONTROLS'],
        'decision':report['decision'],'phase':report['body']['phase'],
        'findings':report['findings'],'evidence_body':report['body']}


def audit(report,event,extra=None):
    log_event(report['body']['workflow_id'],'policy_engine',event,{**audit_payload(report),**(extra or {})})


def valid_approval(report,receipt):
    expected={'evidence_hash':report['evidence_hash'],'policy_hash':report['body']['policy_hash'],
              'workflow_id':report['body']['workflow_id'],'workspace':report['body']['workspace'],
              'decision':'approve','policy_ids':sorted({f['policy_id'] for f in report['findings']})}
    if not isinstance(receipt,dict) or receipt!=expected:return False
    try:
        with sqlite3.connect(Path(audit_logger.DB_PATH).resolve().as_uri()+'?mode=ro',uri=True) as conn:
            rows=conn.execute("SELECT event,details FROM audit_events WHERE workflow_id=? AND event IN ('POLICY_EXCEPTION_APPROVED','POLICY_EXCEPTION_REJECTED') ORDER BY id",
                             (report['body']['workflow_id'],)).fetchall()
        decisions=[(event,json.loads(raw)) for event,raw in rows]
        matching=[(event,detail) for event,detail in decisions if detail.get('evidence_hash')==report['evidence_hash']]
        return bool(matching and matching[-1][0]=='POLICY_EXCEPTION_APPROVED' and matching[-1][1]=={**audit_payload(report),'approval_reference':receipt})
    except (OSError,ValueError,TypeError,AttributeError,sqlite3.Error):return False


def enforce(proposals, originals, workspace, workflow_id, *, approvals=(), phase='pre_write',allow_new_test_files=(),allow_new_source_files=()):
    try:
        report=evaluate(proposals,originals,workspace,workflow_id,phase=phase,allow_new_test_files=allow_new_test_files,allow_new_source_files=allow_new_source_files)
    except Exception as error:
        log_event(workflow_id,'policy_engine','POLICY_EVALUATION_ERROR',{'policy_ids':['GOV-UNSCANNABLE'],
                  'decision':'deny','phase':phase,'error_type':type(error).__name__})
        raise
    reference=next((r for r in approvals if valid_approval(report,r)),None)
    audit(report,'POLICY_EVALUATED',{'approval_reference':reference,
          'enforced_decision':'allow_with_approval' if report['decision']=='requires_approval' and reference else report['decision']})
    if report['decision']=='deny':raise PolicyViolation(report)
    if report['decision']=='requires_approval' and reference is None:raise PolicyApprovalRequired(report)
    return report


def build_review_workflow():
    """Explicit review of a frozen proposal; callers still need plan/file approval.

    No writes or model calls. The caller applies the exact saved proposal using
    existing execution authorization plus the returned policy approval receipt.
    """
    from langgraph.graph import StateGraph,START,END
    from langgraph.types import interrupt
    def review(state):
        kwargs={'phase':state.get('phase','pre_write'),'allow_new_test_files':state.get('allow_new_test_files',()),'allow_new_source_files':state.get('allow_new_source_files',())}
        report=evaluate(state['proposals'],state['originals'],Path(state['workspace']),state['workflow_id'],**kwargs)
        if report['decision']!='requires_approval':
            audit(report,'POLICY_EXCEPTION_REVIEW_BLOCKED')
            raise PermissionError('Only supported reviewable findings may enter exception review')
        # Verify originals against disk before requesting approval.
        for path,old in state['originals'].items():
            if (Path(state['workspace'])/path).read_bytes()!=old:raise PermissionError('Source changed before policy review')
        audit(report,'POLICY_EXCEPTION_REVIEW_REQUESTED')
        decision=interrupt({'type':'POLICY_EXCEPTION_APPROVAL','report':report,
                            'message':'Approve only the exact static /usr/bin/true process capability. This is not file, plan or release approval.'})
        if decision!='approve':
            audit(report,'POLICY_EXCEPTION_REJECTED');return {**state,'status':'STOPPED_POLICY_REJECTED'}
        receipt={'evidence_hash':report['evidence_hash'],'policy_hash':report['body']['policy_hash'],
                 'workflow_id':report['body']['workflow_id'],'workspace':report['body']['workspace'],
                 'decision':'approve','policy_ids':sorted({f['policy_id'] for f in report['findings']})}
        audit(report,'POLICY_EXCEPTION_APPROVED',{'approval_reference':receipt})
        return {**state,'status':'POLICY_EXCEPTION_APPROVED','policy_approval':receipt}
    graph=StateGraph(dict);graph.add_node('review',review);graph.add_edge(START,'review');graph.add_edge('review',END)
    return graph


def release_controls(state,manifest):
    """Fresh static scan plus original inventory preservation; never a human signoff."""
    from governance.test_safety import load_baseline,inventory,assert_integrity
    origin=state['origin'];root=Path(manifest['workspace']);evidence=Path(origin['evidence'])
    baseline=load_baseline(evidence/'originals',origin['baseline_hash'])
    current=inventory(root)
    # The already independently verified manifest binds source/test changes.
    allowed=set()
    if origin.get('profile') in {'integrated-expiration-v1', 'parallel-expiration-v1'}:
        from governance.plan_lineage import source_journal
        tx_ids = origin.get('transactions') or ([origin['source_transaction']] if isinstance(origin.get('source_transaction'), str) else origin.get('source_transaction', []))
        for tx_id in tx_ids:
            journal=source_journal(root, tx_id)
            allowed.update(e['path'] for e in journal['files'])
        allowed.add(manifest['test_path'])
    else:
        allowed.add(origin['scope']['production_file']);allowed.add(origin['scope']['new_test_file'])
    changed={p:v for p,v in current.items() if baseline.get(p)!=v}
    if not set(changed)<=allowed:raise PermissionError('Policy release inventory has unauthorized changes')
    assert_integrity(root,baseline,changed)
    files={p:v for p,v in current.items() if p.endswith('.java')}
    report=enforce(files,{p:baseline.get(p) for p in files},root,state['run_id'],phase='release',
                   approvals=state.get('release_policy_approvals',()),allow_new_test_files=[p for p in allowed if p not in baseline])
    return {'static_security_compliance_controls':True,'protected_inventory_and_tests':True,
            'operational_validation_evidence': manifest['records']['positive'].get('exit_code')==0 and bool(manifest['records']['positive']['cases']) and
                all(v=='passed' for v in manifest['records']['positive']['cases'].values()),
            'policy_evidence_hash':report['evidence_hash'],'policy_hash':report['body']['policy_hash']}
