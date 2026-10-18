"""Read-only, evidence-qualified metrics. This module never resumes workflows.

Durations are wall-clock observations, not CPU time. Unknowns are JSON null,
not zero. Cohorts are supplied provenance labels, never inferred as production.
"""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

PASS = {'VALIDATION_PASSED', 'TEST_STAGE_EXIT_PASSED', 'PARALLEL_VALIDATION_PASSED'}
FAIL = {'VALIDATION_FAILED', 'IMPLEMENTATION_SOURCE_GATE_FAILED', 'PARALLEL_ATTEMPT_FAILED',
        'IMPLEMENTATION_BLOCKED', 'PARALLEL_FAILED', 'INTEGRATED_STAGE_STOPPED'}
ROLLBACK = {'TRANSACTION_ROLLED_BACK', 'TRANSACTION_APPLY_ROLLED_BACK', 'ROLLBACK_COMPLETED',
            'PARALLEL_ROLLED_BACK', 'REPAIR_ROLLBACK_COMPLETED'}
RETRY = {'IMPLEMENTATION_RETRY_STARTED', 'INTEGRATED_SAME_PLAN_RETRY'}
WAIT_OPEN = {'PLAN_CREATED': 'plan', 'AWAITING_TEST_GENERATION_APPROVAL': 'test_scope',
             'AWAITING_FINAL_HUMAN_REVIEW': 'final_review'}
WAIT_STATE = {'AWAITING_DESIGN_APPROVAL': 'design', 'AWAITING_TEST_RESULTS_REVIEW': 'test_results',
              'AWAITING_FINAL_HUMAN_REVIEW': 'final_review', 'AWAITING_TEST_GENERATION_APPROVAL': 'test_scope'}
WAIT_CLOSE = {'PLAN_APPROVED': 'plan', 'PLAN_REJECTED': 'plan', 'PLAN_CHANGES_REQUESTED': 'plan',
              'DESIGN_APPROVED': 'design', 'DESIGN_REJECTED': 'design', 'DESIGN_CHANGES_REQUESTED': 'design',
              'TEST_SCOPE_APPROVED': 'test_scope', 'DESIGN_SOURCE_CORRECTION_APPROVED': 'test_scope', 'TEST_RESULTS_APPROVED': 'test_results',
              'TEST_RESULTS_REJECTED': 'test_results', 'FINAL_RELEASE_REVIEW_RECORDED': 'final_review'}
# Explicit execution intervals only; derived stage-boundary spans are separate.
STAGE_START = {'IMPLEMENTATION_ATTEMPT_STARTED': 'implementation', 'PARALLEL_ATTEMPT_STARTED': 'parallel_proposal',
               'PARALLEL_VALIDATION_STARTED': 'parallel_validation'}
STAGE_END = {'IMPLEMENTATION_COMPLETED': 'implementation', 'IMPLEMENTATION_BLOCKED': 'implementation',
             'PARALLEL_TASK_PREPARED': 'parallel_proposal', 'PARALLEL_ATTEMPT_FAILED': 'parallel_proposal',
             'PARALLEL_VALIDATION_PASSED': 'parallel_validation'}


def stamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None: return None
        return dt.timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError): return None


def metric(value=None, reason=None, **extra):
    return {'value': value, 'unavailable_reason': reason if value is None else None, **extra}


def union_seconds(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]: merged[-1][1] = max(end, merged[-1][1])
        else: merged.append([start, end])
    return sum(end-start for start, end in merged)


def read_db(path, query):
    path = Path(path).resolve(strict=True)
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        return conn.execute(query).fetchall()


def read_events(path):
    rows = read_db(path, 'SELECT id,workflow_id,timestamp,agent,event,details FROM audit_events ORDER BY id')
    result = []
    for identifier, workflow, timestamp, agent, event, raw in rows:
        error = None
        try:
            details = json.loads(raw)
            if not isinstance(details, dict): raise ValueError('Expected details object')
        except (ValueError, TypeError): details = {}; error = 'Malformed event details'
        result.append({'id': identifier, 'workflow_id': workflow, 'timestamp': timestamp,
                       'time': stamp(timestamp), 'agent': agent, 'event': event, 'details': details, 'error': error})
    return result


def read_checkpoints(path):
    if path is None: return []
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    rows = read_db(path, 'SELECT thread_id,checkpoint_ns,checkpoint_id,type,checkpoint FROM checkpoints ORDER BY checkpoint_id')
    result = []
    for workflow, namespace, identifier, kind, raw in rows:
        item = {'workflow_id': workflow, 'namespace': namespace, 'id': identifier}
        try:
            if kind not in {'json', 'msgpack'}: raise ValueError('Unsupported checkpoint encoding')
            checkpoint = JsonPlusSerializer().loads_typed((kind, raw))
            channels = checkpoint.get('channel_values', {})
            state = channels.get('__root__', channels)
            nested = state.get('integrated_result') or state.get('parallel_result') or {}
            item.update(time=stamp(checkpoint.get('ts')), timestamp=checkpoint.get('ts'),
                        status=state.get('status'), review_hash=(state.get('bundle_hash') if state.get('status')=='AWAITING_TEST_RESULTS_REVIEW' else state.get('design_hash') if state.get('status')=='AWAITING_DESIGN_APPROVAL' else None), source_attempt=state.get('source_attempt', nested.get('source_attempt')))
        except Exception as error: item.update(time=None, error=str(error))
        result.append(item)
    return result


def elapsed(start, end, reason):
    if start is None or end is None or end < start: return metric(reason=reason)
    return metric(round(end-start, 6))


def analyze(events, checkpoints=(), *, max_attempts=None):
    """One database branch/thread, preserving causal audit order and event IDs."""
    if max_attempts is not None and (type(max_attempts) is not int or max_attempts < 1):
        raise ValueError('max_attempts must be a positive supplied policy integer')
    issues = [{'event_id': e['id'], 'issue': e['error']} for e in events if e.get('error')]
    for e in events:
        if e['time'] is None: issues.append({'event_id': e['id'], 'issue': 'Missing/invalid timezone-aware timestamp'})
    valid_times = [e['time'] for e in events if e['time'] is not None]
    reversed_clock = any(b < a for a, b in zip(valid_times, valid_times[1:]))
    if reversed_clock: issues.append({'issue': 'Audit clock regressed; timing metrics withheld'})
    for cp in checkpoints:
        if cp.get('error'): issues.append({'checkpoint_id': cp['id'], 'issue': cp['error']})
    cps = sorted((cp for cp in checkpoints if cp.get('time') is not None), key=lambda cp: cp['time'])
    latest = cps[-1] if cps else None
    status = latest.get('status') if latest else None
    if not status:
        statuses = [e['details']['status'] for e in events if e['event']=='WORKFLOW_FINISHED' and isinstance(e['details'].get('status'), str)]
        status = statuses[-1] if statuses else None
    terminal = bool(status and (status.startswith('STOPPED') or status in {'RELEASED', 'FINAL_REVIEW_RECORDED'}))
    # AWAITING_* is an open handoff, even when a CLI emits WORKFLOW_FINISHED.
    signals = [e for e in events if e['event'] in PASS | FAIL and not e.get('error')]
    engineering = ('passed' if signals[-1]['event'] in PASS else 'failed') if signals else 'unavailable'
    if status and status.startswith('STOPPED') and status != 'STOPPED_RELEASE_BLOCKED' and engineering == 'passed':
        engineering = 'unavailable'  # Later stop may invalidate an earlier success.
    if engineering == 'failed' and not terminal:
        engineering = 'unavailable'  # An interrupted retry has no final failure outcome yet.
    readiness = [e for e in events if e['event']=='RELEASE_READINESS_EVALUATED']
    blocked = status == 'STOPPED_RELEASE_BLOCKED' or bool(readiness and readiness[-1]['details'].get('status')=='BLOCKED')
    grants = {e['details'].get('authorization_id') for e in events if e['event']=='RELEASE_AUTHORIZED' and e['details'].get('decision')=='approve' and isinstance(e['details'].get('authorization_id'), str)}
    releases = [e for e in events if e['event']=='RELEASE_COMPLETED' and isinstance(e['details'].get('authorization_id'),str) and e['details']['authorization_id'] in grants]
    released = bool(releases) and not blocked

    transactions = {e['details']['transaction_id'] for e in events if e['event']=='TRANSACTION_APPLIED' and isinstance(e['details'].get('transaction_id'), str)}
    rolled = {e['details']['transaction_id'] for e in events if e['event'] in ROLLBACK and isinstance(e['details'].get('transaction_id'), str)}
    unidentified = [e['id'] for e in events if e['event'] in ROLLBACK and not isinstance(e['details'].get('transaction_id'), str)]
    unknown_rollbacks = rolled-transactions
    rollback_rate = metric(len(rolled)/len(transactions)) if transactions and not unidentified and not unknown_rollbacks else metric(reason='Missing application IDs or rollback transaction identity')

    retries = [e for e in events if e['event'] in RETRY or (e['event']=='PARALLEL_ATTEMPT_STARTED' and isinstance(e['details'].get('attempt'), int) and e['details']['attempt']>1)]
    model_calls = Counter((e['details'].get('stage') if isinstance(e['details'].get('stage'),str) else 'unknown') for e in events if e['event']=='MODEL_RESPONSE_RECORDED')
    # Documentation retries are evidenced by a rejection followed by another call.
    doc_rejected = False
    for e in events:
        if e['event']=='DOCUMENTATION_GATE_REJECTED': doc_rejected = True
        if e['event']=='MODEL_RESPONSE_RECORDED' and e['details'].get('stage')=='documentation' and doc_rejected:
            retries.append(e); doc_rejected = False
    observed_attempts = [e['details']['attempt'] for e in events if e['event'] in {'IMPLEMENTATION_ATTEMPT_STARTED','PARALLEL_ATTEMPT_STARTED','IMPLEMENTATION_SOURCE_GATE_PASSED','IMPLEMENTATION_SOURCE_GATE_FAILED'} and isinstance(e['details'].get('attempt'),int)]
    exhausted_events = [e['id'] for e in events if e['event']=='SEMANTIC_PLAN_EXHAUSTED' or (e['event']=='RECOVERY_STOPPED' and e['details'].get('reason')=='RETRY_LIMIT_EXHAUSTED')]
    bound = metric(reason='No supplied policy bound or observed attempt ordinal')
    if max_attempts is not None and observed_attempts:
        bound = metric(max(observed_attempts)<=max_attempts, configured_max_attempts=max_attempts, observed_max_attempt=max(observed_attempts))
    exhaustion = bool(exhausted_events) or bool(max_attempts and observed_attempts and max(observed_attempts)>=max_attempts and engineering=='failed' and terminal)

    recovery = []; pending = None
    for e in events:
        if e['event'] in FAIL:
            if pending is None: pending = e
        elif e['event'] in PASS and pending is not None:
            recovery.append({'failure_event_id': pending['id'], 'recovered_event_id': e['id'],
                             **elapsed(pending['time'], e['time'], 'Missing or reversed recovery timestamp')})
            pending = None
    recovery_values = [r['value'] for r in recovery if r['value'] is not None]
    mttr = metric(round(sum(recovery_values)/len(recovery_values),6)) if recovery_values else metric(reason='No completed failure-to-engineering-validation recovery interval')

    waits = []; stages = []; openings = {}; active = {}; unmatched_ends = []
    timeline = [(e['time'], 1, e) for e in events if e['time'] is not None]
    timeline += [(cp['time'], 0, cp) for cp in cps]
    for t, kind, item in sorted(timeline, key=lambda row:(row[0],row[1])):
        if kind == 0:
            if item.get('status') in WAIT_STATE:
                label = WAIT_STATE[item['status']]
                openings.setdefault(label, (t, 'checkpoint:'+item['id'],item.get('review_hash')))
            elif str(item.get('status','')).startswith('STOPPED'):
                openings.clear()
            continue
        name = item['event']; d = item['details']; ref = 'audit:'+str(item['id'])
        if name in {'REQUIREMENTS_CHANGED','DOWNSTREAM_PLANS_INVALIDATED'}: openings.clear()
        if name == 'INTEGRATED_STAGE_STOPPED': openings.pop('test_scope',None)
        if name in WAIT_OPEN: openings.setdefault(WAIT_OPEN[name], (t,ref,d.get('plan_hash') if name=='PLAN_CREATED' else None))
        if name in WAIT_CLOSE:
            label = WAIT_CLOSE[name]; start = openings.pop(label, None)
            close_hash=d.get('bundle_hash') if label=='test_results' else d.get('design_hash') if label=='design' else d.get('plan_hash') if label=='plan' else None
            if start and start[2] and close_hash and start[2]!=close_hash:
                openings[label]=start;unmatched_ends.append(ref)
                issues.append({'event_id':item['id'],'issue':'Review hash mismatch; wait interval withheld'})
                continue
            if start: waits.append({'stage':label,'start':start[0],'end':t,'start_ref':start[1],'end_ref':ref,'seconds':round(t-start[0],6)})
            else: unmatched_ends.append(ref)
        if name in {'STAGE_GATE_PASSED','STAGE_GATE_REJECTED'} and d.get('stage')=='design':
            if d.get('gate')=='entry' and name=='STAGE_GATE_PASSED': active[('design',None,None)]=(t,ref)
            elif d.get('gate')=='exit':
                start=active.pop(('design',None,None),None)
                if start: stages.append({'stage':'design','start':start[0],'end':t,'start_ref':start[1],'end_ref':ref,'seconds':round(t-start[0],6)})
        if name in STAGE_START:
            label = STAGE_START[name]; key = (label, d.get('task_id', (d.get('task_ids') or [None])[0]), d.get('attempt'))
            active[key] = (t,ref)
        if name in STAGE_END:
            label = STAGE_END[name]; key = (label, d.get('task_id', (d.get('task_ids') or [None])[0]), d.get('attempt'))
            start = active.pop(key, None)
            if start: stages.append({'stage':label,'start':start[0],'end':t,'start_ref':start[1],'end_ref':ref,'seconds':round(t-start[0],6)})
    # Stage boundaries provide useful elapsed evidence without claiming CPU/model time.
    boundary = []
    for start_name, end_names, label in [('INTEGRATED_IMPLEMENTATION_APPLIED', {'IMPLEMENTATION_SOURCE_GATE_PASSED','IMPLEMENTATION_SOURCE_GATE_FAILED'}, 'source_validation_boundary'),
                                         ('TEST_RESULTS_APPROVED', {'DOCUMENTATION_GATE_PASSED'}, 'documentation_boundary'),
                                         ('TEST_SCOPE_APPROVED', {'TEST_FILE_CREATED'}, 'test_generation_boundary'),
                                         ('TEST_FILE_CREATED', {'TEST_STAGE_EXIT_PASSED'}, 'combined_validation_boundary'),
                                         ('DOCUMENTATION_GATE_PASSED', {'RELEASE_READINESS_EVALUATED'}, 'readiness_boundary')]:
        start = None
        for e in events:
            if e['event']==start_name: start=e
            elif e['event'] in end_names and start:
                value=elapsed(start['time'],e['time'],'Missing boundary timestamp')
                if value['value'] is not None: boundary.append({'stage':label,'seconds':value['value'],'start_ref':'audit:'+str(start['id']),'end_ref':'audit:'+str(e['id'])})
                start=None
    start_event = next((e for e in events if e['event']=='WORKFLOW_STARTED'), None)
    end_event = next((e for e in reversed(events) if e['event']=='WORKFLOW_FINISHED' and e['details'].get('status')==status), None) if terminal else None
    end_time = end_event['time'] if end_event else latest['time'] if latest and terminal else None
    latency = elapsed(start_event['time'] if start_event else None, end_time, 'Exact workflow start or terminal completion missing; pending review is not completion')
    observed = [*valid_times, *[cp['time'] for cp in cps]]
    window = metric(round(max(observed)-min(observed),6)) if observed else metric(reason='No valid observation timestamps')
    timing_bad = reversed_clock or any(e['time'] is None for e in events)
    if timing_bad:
        latency=metric(reason='Incomplete or regressing audit timestamps'); mttr=metric(reason='Incomplete or regressing audit timestamps')
    if timing_bad:
        for sample in recovery: sample.update(value=None, unavailable_reason='Incomplete or regressing audit timestamps')
        for interval in waits + stages: interval['seconds'] = None
        for interval in boundary: interval['seconds'] = None
    wait_total = metric(round(union_seconds([(w['start'],w['end']) for w in waits]),6)) if waits and not timing_bad else metric(reason='No complete reliable review-ready/decision pairs')
    execution = metric(round(union_seconds([(v['start'],v['end']) for v in stages]),6)) if stages and not timing_bad else metric(reason='No complete explicit agent start/end pairs')
    stage_durations={}
    for label in ('requirements','codebase','planning','design','implementation','parallel_proposal','parallel_validation',
                  'source_validation_boundary','test_generation_boundary','combined_validation_boundary','documentation_boundary','readiness_boundary'):
        explicit=[i for i in stages if i['stage']==label and i['seconds'] is not None]
        derived=[i for i in boundary if i['stage']==label and i['seconds'] is not None]
        value=union_seconds([(i['start'],i['end']) for i in explicit]) if explicit else sum(i['seconds'] for i in derived) if derived else None
        stage_durations[label]=metric(round(value,6) if value is not None else None, 'No reliable matching stage boundaries',
                                      samples=len(explicit)+len(derived),kind='derived boundary wall time' if label.endswith('_boundary') else 'explicit execution wall time')
    # Never label workflow elapsed minus approval waits as agent execution.
    return {'status':status,'terminal':terminal,'engineering_validation':engineering,
            'engineering_evidence_event_id': signals[-1]['id'] if signals else None,
            'release':{'blocked':blocked,'authorization_evidenced':bool(grants) and not blocked,'released':released,
                       'reason':'Requires matching explicit release authorization and completion events; validation is insufficient'},
            'retry':{'observed_automatic_retry_count':len(retries),'event_ids':[e['id'] for e in retries],
                     'count_scope':'Known explicit retry vocabulary; lower bound when attempt events are absent',
                     'model_response_counts_by_stage':dict(model_calls),
                     'human_source_corrections':sum(e['event']=='DESIGN_SOURCE_CORRECTION_STARTED' for e in events),
                     'exact_source_replays':sum(e['event']=='TEST_STAGE_EXACT_REPLAY_APPROVED' for e in events),
                     'bound_check':bound,'exhausted':exhaustion,'explicit_exhaustion_event_ids':exhausted_events,
                     'exhaustion_basis':'Explicit event or terminal failure at supplied policy bound' if exhaustion else 'Not evidenced; absence does not establish remaining budget'},
            'rollback':{'applied_transaction_count':len(transactions),'rolled_back_transaction_count':len(rolled),
                        'rolled_back_transaction_ids':sorted(rolled),'unidentified_event_ids':unidentified,
                        'rollback_frequency_per_applied_transaction':rollback_rate},
            'mttr_seconds':mttr,'recovery_intervals':recovery,'unrecovered_failure_event_id':pending['id'] if pending else None,
            'end_to_end_latency_seconds':latency,'observed_window_seconds':window,
            'stage_durations_seconds':stage_durations,
            'timing':{'human_review_ready_to_decision_seconds':wait_total,'human_wait_intervals':waits,
                      'unclosed_review_stages':sorted(openings),'decisions_without_request_timestamp':unmatched_ends,
                      'agent_execution_wall_seconds':execution,'agent_stage_intervals':stages,
                      'incomplete_agent_intervals':len(active),'derived_stage_boundary_durations':boundary,
                      'unattributed_time':'Not reported as agent time; manual pauses, scheduling and missing instrumentation remain unclassified',
                      'quality':'unavailable' if timing_bad else 'partial'},
            'issues':issues,'event_count':len(events),'checkpoint_count':len(checkpoints)}


def report(specs):
    workflows=[]; seen=set()
    for spec in specs:
        path=Path(spec['audit_db']).resolve(strict=True)
        events=read_events(path); checkpoints=read_checkpoints(spec.get('workflow_db'))
        ids=[spec['workflow_id']] if spec.get('workflow_id') else sorted({e['workflow_id'] for e in events})
        for wid in ids:
            key=(str(path),wid)
            if key in seen: raise ValueError('Duplicate database/thread selection would double-count workflows')
            seen.add(key)
            selected=[e for e in events if e['workflow_id']==wid]; cp=[c for c in checkpoints if c['workflow_id']==wid]
            item=analyze(selected,cp,max_attempts=spec.get('max_attempts'))
            item.update(workflow_id=wid,audit_db=str(path),workflow_db=spec.get('workflow_db'),
                        cohort=spec['cohort'],provenance=spec['provenance'],
                        audit_snapshot_sha256=hashlib.sha256(json.dumps(selected,sort_keys=True).encode()).hexdigest(),
                        checkpoint_projection_sha256=hashlib.sha256(json.dumps(cp,sort_keys=True).encode()).hexdigest())
            workflows.append(item)
    cohorts={}
    for cohort in sorted({w['cohort'] for w in workflows}):
        rows=[w for w in workflows if w['cohort']==cohort]; evaluable=[w for w in rows if w['engineering_validation']!='unavailable']
        passed=sum(w['engineering_validation']=='passed' for w in evaluable)
        recovered=[r['value'] for w in rows for r in w['recovery_intervals'] if r['value'] is not None and w['mttr_seconds']['value'] is not None]
        latencies=[w['end_to_end_latency_seconds']['value'] for w in rows if w['end_to_end_latency_seconds']['value'] is not None]
        cohorts[cohort]={'selected_workflows':len(rows),'engineering_evaluable_workflows':len(evaluable),
            'engineering_passed_workflows':passed,'engineering_success_rate':metric(passed/len(evaluable)) if evaluable else metric(reason='No observed engineering outcomes'),
            'engineering_unknown_workflows':len(rows)-len(evaluable),
            'workflows_with_observed_automatic_retry':sum(w['retry']['observed_automatic_retry_count']>0 for w in rows),
            'observed_retry_workflow_frequency':metric(sum(w['retry']['observed_automatic_retry_count']>0 for w in rows)/len(rows)),
            'workflows_with_rollback':sum(w['rollback']['rolled_back_transaction_count']>0 for w in rows),
            'observed_rollback_workflow_frequency':metric(sum(w['rollback']['rolled_back_transaction_count']>0 for w in rows)/len(rows)),
            'mttr_seconds':metric(round(sum(recovered)/len(recovered),6)) if recovered else metric(reason='No complete recovery samples'),
            'latency_samples':len(latencies),
            'mean_end_to_end_latency_seconds':metric(round(sum(latencies)/len(latencies),6)) if latencies else metric(reason='No complete workflow start/terminal pairs'),
            'released_workflows':sum(w['release']['released'] for w in rows),
            'release_blocked_workflows':sum(w['release']['blocked'] for w in rows)}
    return {'schema_version':1,'scope':'Selected retained database branches; not a production reliability estimate',
            'definitions':{'success_rate':'Latest observed engineering outcome; unknown outcomes excluded with coverage counts. Not release success.',
                'mttr':'First engineering failure in an incident through next passing required engineering validation; includes review/idle time. Unresolved incidents excluded and listed.',
                'latency':'WORKFLOW_STARTED through terminal audit/checkpoint completion only. Imported/open workflows unavailable.',
                'execution_time':'Union of explicitly paired agent start/end wall intervals, not CPU time; parallel intervals not double-counted.',
                'human_wait':'Review-ready audit/checkpoint to decision wall time, including scheduling/resume overhead; partial coverage only.',
                'rollback':'Unique transaction IDs; duplicate wrapper events are not additional rollbacks.',
                'provenance':'Caller-supplied cohort labels; copied/forked databases are branch observations, not independent production samples.'},
            'cohorts':cohorts,'workflows':workflows}


def render(report):
    lines=['# Audit-backed reliability metrics','',report['scope']+'.','']
    for name,c in report['cohorts'].items():
        rate=c['engineering_success_rate']['value']
        lines += [f'## {name}', '',f"Engineering validation: {c['engineering_passed_workflows']}/{c['engineering_evaluable_workflows']} observed outcomes passed ({rate if rate is not None else 'unavailable'}); {c['engineering_unknown_workflows']} unknown.",
                  f"Released workflows evidenced: {c['released_workflows']}; blocked releases: {c['release_blocked_workflows']}.",
                  f"Observed retry workflow frequency: {c['workflows_with_observed_automatic_retry']}/{c['selected_workflows']} ({c['observed_retry_workflow_frequency']['value']}).",
                  f"Observed rollback workflow frequency: {c['workflows_with_rollback']}/{c['selected_workflows']} ({c['observed_rollback_workflow_frequency']['value']}).",
                  f"Completed-incident MTTR: {c['mttr_seconds']['value'] if c['mttr_seconds']['value'] is not None else 'unavailable'} seconds.",
                  f"Mean complete workflow latency: {c['mean_end_to_end_latency_seconds']['value'] if c['mean_end_to_end_latency_seconds']['value'] is not None else 'unavailable'} seconds ({c['latency_samples']} samples).",'']
    for w in report['workflows']:
        def show(m): return str(m['value'])+' seconds' if m['value'] is not None else 'unavailable — '+m['unavailable_reason']
        lines += [f"## {w['workflow_id']} ({w['cohort']})",'',f"Status: {w['status'] or 'unavailable'}. Engineering: {w['engineering_validation']}. Released: {w['release']['released']}.",
                  f"Automatic retries observed: {w['retry']['observed_automatic_retry_count']}; unique rollbacks: {w['rollback']['rolled_back_transaction_count']}.",
                  'MTTR: '+show(w['mttr_seconds'])+'.','End-to-end latency: '+show(w['end_to_end_latency_seconds'])+'.',
                  'Measured review-ready wait: '+show(w['timing']['human_review_ready_to_decision_seconds'])+'.',
                  'Explicit agent execution intervals (union): '+show(w['timing']['agent_execution_wall_seconds'])+'.','']
        lines += ['Unavailable stage timings: '+', '.join(k for k,v in w['stage_durations_seconds'].items() if v['value'] is None)+'.','']
        intervals=w['timing']['agent_stage_intervals'] + w['timing']['derived_stage_boundary_durations']
        if intervals:
            lines += ['| Measured stage | Seconds | Evidence |','| --- | --- | --- |']
            lines += [f"| {i['stage']} | {i['seconds'] if i['seconds'] is not None else 'unavailable'} | {i['start_ref']} → {i['end_ref']} |" for i in intervals]
            lines += ['']
    lines += ['Timing coverage is partial. Stage intervals, unresolved incidents, missing review boundaries, policy checks, provenance and source hashes are in the JSON report. Unattributed elapsed time is not agent execution time. Historical databases were opened read-only.','']
    return '\n'.join(lines)
