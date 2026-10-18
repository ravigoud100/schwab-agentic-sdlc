"""Independent SQL/arithmetic checks for the retained assessment selection.

Read-only verification; do not use these selected examples as population estimates.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3


def verify(result):
    checks=[]
    for workflow in result['workflows']:
        with sqlite3.connect(Path(workflow['audit_db']).as_uri()+'?mode=ro',uri=True) as conn:
            rows=conn.execute('select id,timestamp,event,details from audit_events where workflow_id=? order by id',
                              (workflow['workflow_id'],)).fetchall()
        events=[(i,datetime.fromisoformat(t),e,json.loads(d)) for i,t,e,d in rows]
        # Deliberately independent of the production module's constants/helpers.
        rollback_ids={d['transaction_id'] for _,_,e,d in events if e in
                      {'TRANSACTION_ROLLED_BACK','TRANSACTION_APPLY_ROLLED_BACK','ROLLBACK_COMPLETED','PARALLEL_ROLLED_BACK','REPAIR_ROLLBACK_COMPLETED'} and isinstance(d.get('transaction_id'),str)}
        assert len(rollback_ids)==workflow['rollback']['rolled_back_transaction_count']
        checks.append({'workflow_id':workflow['workflow_id'],'check':'unique_rollback_transactions','value':len(rollback_ids)})
        if workflow['workflow_id']=='e43f07a6-4824-4f49-865f-69ae19899ca1':
            assert workflow['status']=='STOPPED_RELEASE_BLOCKED'
            assert workflow['engineering_validation']=='passed' and not workflow['release']['released']
            assert workflow['retry']['human_source_corrections']==1
            assert workflow['retry']['exact_source_replays']==1
            assert workflow['retry']['observed_automatic_retry_count']==2
            assert workflow['end_to_end_latency_seconds']['value'] is None
            assert not any(e=='WORKFLOW_STARTED' for _,_,e,_ in events)
            start=next(t for _,t,e,_ in events if e=='IMPLEMENTATION_SOURCE_GATE_FAILED')
            end=next(t for _,t,e,_ in events if e=='TEST_STAGE_EXIT_PASSED')
            assert abs(workflow['mttr_seconds']['value']-(end-start).total_seconds())<0.000002
            checks.append({'workflow_id':workflow['workflow_id'],'check':'blocked_not_released_and_recovery_arithmetic','seconds':(end-start).total_seconds()})
        if workflow['workflow_id']=='309b0b75-72f2-4ad3-8df6-6b520c88ab52':
            start=next(t for _,t,e,_ in events if e=='VALIDATION_FAILED')
            end=next(t for _,t,e,_ in events if e=='VALIDATION_PASSED')
            assert abs(workflow['mttr_seconds']['value']-(end-start).total_seconds())<0.000002
            assert workflow['end_to_end_latency_seconds']['value'] is None
            checks.append({'workflow_id':workflow['workflow_id'],'check':'live_recovery_mttr','seconds':(end-start).total_seconds()})
        if workflow['workflow_id']=='parallel-fixture-success':
            started={d['task_id']:t for _,t,e,d in events if e=='PARALLEL_ATTEMPT_STARTED'}
            ended={d['task_id']:t for _,t,e,d in events if e=='PARALLEL_TASK_PREPARED'}
            earliest=min(started.values());latest=max(ended.values())
            intervals=workflow['timing']['agent_stage_intervals']
            sum_worker=sum(i['seconds'] for i in intervals if i['stage']=='parallel_proposal')
            union_worker=workflow['stage_durations_seconds']['parallel_proposal']['value']
            assert sum_worker>union_worker
            assert abs(union_worker-(latest-earliest).total_seconds())<0.000002
            checks.append({'workflow_id':workflow['workflow_id'],'check':'concurrent_worker_union','union_seconds':union_worker,'sum_seconds':sum_worker})
    return {'passed':True,'checks':checks,'scope':'Retained examples; audit interpretation and arithmetic, not production reliability'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('report',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();checked=verify(json.loads(args.report.read_text()))
    with args.output.open('x') as stream:stream.write(json.dumps(checked,indent=2)+'\n')
    print(json.dumps(checked,indent=2))
