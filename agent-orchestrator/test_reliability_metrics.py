"""Synthetic timestamp fixtures validate arithmetic, not production reliability."""
from datetime import datetime,timedelta,timezone
import hashlib
import json
import sqlite3

import pytest
from governance.reliability_metrics import analyze,read_events,report,stamp,union_seconds,render

START=datetime(2026,1,1,tzinfo=timezone.utc)


def events(rows):
    return [{'id':i,'workflow_id':'w','timestamp':(START+timedelta(seconds=t)).isoformat(),
             'time':(START+timedelta(seconds=t)).timestamp(),'agent':'test','event':e,'details':d,'error':None}
            for i,(t,e,d) in enumerate(rows,1)]


def cps(status,time):
    return [{'id':'cp','namespace':'','workflow_id':'w','time':START.timestamp()+time,'status':status}]


def test_success_and_blocked_release_are_separate_with_exact_latency():
    es=events([(0,'WORKFLOW_STARTED',{}),(1,'PLAN_CREATED',{}),(11,'PLAN_APPROVED',{}),
               (12,'IMPLEMENTATION_ATTEMPT_STARTED',{'attempt':1,'task_ids':['a']}),
               (17,'IMPLEMENTATION_COMPLETED',{'attempt':1,'task_id':'a'}),
               (20,'VALIDATION_PASSED',{}),(22,'RELEASE_READINESS_EVALUATED',{'status':'BLOCKED'}),
               (30,'WORKFLOW_FINISHED',{'status':'STOPPED_RELEASE_BLOCKED'})])
    m=analyze(es)
    assert m['engineering_validation']=='passed' and m['release']['released'] is False
    assert m['release']['blocked'] is True
    assert m['end_to_end_latency_seconds']['value']==30
    assert m['timing']['human_review_ready_to_decision_seconds']['value']==10
    assert m['timing']['agent_execution_wall_seconds']['value']==5
    assert m['mttr_seconds']['value'] is None


def test_retry_rollback_deduplication_and_mttr_arithmetic():
    es=events([(0,'WORKFLOW_STARTED',{}),(1,'TRANSACTION_APPLIED',{'transaction_id':'t1'}),
               (5,'VALIDATION_FAILED',{}),(6,'TRANSACTION_ROLLED_BACK',{'transaction_id':'t1'}),
               (6.1,'ROLLBACK_COMPLETED',{'transaction_id':'t1'}),
               (7,'IMPLEMENTATION_RETRY_STARTED',{'attempt':2}),
               (8,'TRANSACTION_APPLIED',{'transaction_id':'t2'}),(25,'VALIDATION_PASSED',{})])
    m=analyze(es,cps('AWAITING_FINAL_HUMAN_REVIEW',26),max_attempts=3)
    assert m['engineering_validation']=='passed'
    assert m['rollback']['rolled_back_transaction_count']==1
    assert m['rollback']['rollback_frequency_per_applied_transaction']['value']==0.5
    assert m['retry']['observed_automatic_retry_count']==1
    assert m['mttr_seconds']['value']==20
    assert m['end_to_end_latency_seconds']['value'] is None


def test_exhausted_failure_and_nonrecoverable_stop_are_different():
    es=events([(0,'WORKFLOW_STARTED',{}),(1,'IMPLEMENTATION_ATTEMPT_STARTED',{'attempt':3}),
               (2,'VALIDATION_FAILED',{}),(3,'RECOVERY_STOPPED',{'reason':'RETRY_LIMIT_EXHAUSTED'}),
               (4,'WORKFLOW_FINISHED',{'status':'STOPPED_ROLLED_BACK'})])
    m=analyze(es,max_attempts=3)
    assert m['engineering_validation']=='failed' and m['retry']['exhausted']
    assert m['mttr_seconds']['value'] is None and m['unrecovered_failure_event_id']==3
    assert m['retry']['bound_check']['value'] is True
    es[-2]['details']['reason']='NONRECOVERABLE_INFRASTRUCTURE'
    assert analyze(es)['retry']['exhausted'] is False


def test_open_interrupted_workflow_never_gets_terminal_latency_or_final_failure():
    es=events([(0,'WORKFLOW_STARTED',{}),(4,'VALIDATION_FAILED',{}),
               (5,'IMPLEMENTATION_RETRY_STARTED',{'attempt':2}),(6,'IMPLEMENTATION_ATTEMPT_STARTED',{'attempt':2})])
    m=analyze(es,cps('IMPLEMENTING',8))
    assert not m['terminal'] and m['engineering_validation']=='unavailable'
    assert m['end_to_end_latency_seconds']['value'] is None
    assert m['timing']['agent_execution_wall_seconds']['value'] is None
    assert m['timing']['incomplete_agent_intervals']==1
    assert m['mttr_seconds']['value'] is None


def test_imported_requirements_do_not_invent_start_time():
    es=events([(0,'APPROVED_REQUIREMENT_CONTEXT_IMPORTED',{}),(12,'TEST_STAGE_EXIT_PASSED',{}),
               (15,'WORKFLOW_FINISHED',{'status':'STOPPED_RELEASE_BLOCKED'})])
    m=analyze(es)
    assert m['observed_window_seconds']['value']==15
    assert m['end_to_end_latency_seconds']['value'] is None


def test_overlapping_parallel_intervals_are_not_double_counted():
    es=events([(0,'PARALLEL_ATTEMPT_STARTED',{'task_id':'a','attempt':1}),
               (1,'PARALLEL_ATTEMPT_STARTED',{'task_id':'b','attempt':1}),
               (5,'PARALLEL_TASK_PREPARED',{'task_id':'a','attempt':1}),
               (7,'PARALLEL_TASK_PREPARED',{'task_id':'b','attempt':1})])
    m=analyze(es)
    assert sum(i['seconds'] for i in m['timing']['agent_stage_intervals'])==11
    assert m['timing']['agent_execution_wall_seconds']['value']==7
    assert union_seconds([(0,5),(1,7),(10,11)])==8


def test_human_correction_replay_and_automatic_retry_are_distinct():
    es=events([(0,'INTEGRATED_SAME_PLAN_RETRY',{'attempt':2}),
               (1,'DESIGN_SOURCE_CORRECTION_STARTED',{'next_attempt':3}),
               (2,'TEST_STAGE_EXACT_REPLAY_APPROVED',{}),
               (3,'DOCUMENTATION_GATE_REJECTED',{}),
               (4,'MODEL_RESPONSE_RECORDED',{'stage':'documentation'})])
    m=analyze(es)
    assert m['retry']['observed_automatic_retry_count']==2
    assert m['retry']['human_source_corrections']==1 and m['retry']['exact_source_replays']==1


def test_checkpoint_wait_pair_and_repeated_checkpoints_do_not_double_count():
    es=events([(10,'TEST_RESULTS_APPROVED',{'bundle_hash':'h'})])
    cp=cps('AWAITING_TEST_RESULTS_REVIEW',2)+[{**cps('AWAITING_TEST_RESULTS_REVIEW',3)[0],'id':'cp2'}]
    m=analyze(es,cp)
    assert m['timing']['human_review_ready_to_decision_seconds']['value']==8
    assert len(m['timing']['human_wait_intervals'])==1


@pytest.mark.parametrize('problem',['missing','regression'])
def test_bad_timestamps_withhold_timing_and_report_quality(problem):
    es=events([(0,'WORKFLOW_STARTED',{}),(2,'VALIDATION_FAILED',{}),(4,'VALIDATION_PASSED',{}),
               (6,'WORKFLOW_FINISHED',{'status':'STOPPED_RELEASE_BLOCKED'})])
    es[1]['time']=None if problem=='missing' else START.timestamp()-1
    m=analyze(es)
    assert m['mttr_seconds']['value'] is None and m['end_to_end_latency_seconds']['value'] is None
    assert m['issues'] and m['timing']['quality']=='unavailable'
    assert all(r['value'] is None for r in m['recovery_intervals'])


def test_unidentified_rollback_is_not_a_zero_rate():
    m=analyze(events([(0,'ROLLBACK_COMPLETED',{})]))
    assert m['rollback']['rollback_frequency_per_applied_transaction']['value'] is None
    assert m['rollback']['unidentified_event_ids']==[1]


def database(tmp_path,rows):
    path=tmp_path/'audit.db'
    with sqlite3.connect(path) as conn:
        conn.execute('create table audit_events(id integer, workflow_id text,timestamp text,agent text,event text,details text)')
        conn.executemany('insert into audit_events values(?,?,?,?,?,?)',
                         [(e['id'],e['workflow_id'],e['timestamp'],e['agent'],e['event'],json.dumps(e['details'])) for e in rows])
    return path


def test_sqlite_reads_are_nonmutating_and_malformed_records_are_visible(tmp_path):
    path=database(tmp_path,events([(0,'WORKFLOW_STARTED',{})]))
    with sqlite3.connect(path) as conn:conn.execute("update audit_events set details='broken',timestamp='not a time'")
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    es=read_events(path);m=analyze(es)
    assert len(m['issues'])==2
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_cohorts_exclude_unknown_outcomes_and_keep_fixture_rates_separate(tmp_path):
    es=events([(0,'WORKFLOW_STARTED',{}),(2,'VALIDATION_PASSED',{})])
    path=database(tmp_path,es)
    specs=[{'audit_db':str(path),'workflow_id':'w','cohort':'controlled','provenance':'synthetic arithmetic fixture'}]
    result=report(specs);c=result['cohorts']['controlled']
    assert c['engineering_success_rate']['value']==1 and c['released_workflows']==0
    assert 'not a production' in render(result)
    with pytest.raises(ValueError,match='Duplicate'):report(specs+specs)


def test_release_needs_matching_explicit_authorization_and_completion():
    es=events([(0,'VALIDATION_PASSED',{}),(1,'RELEASE_AUTHORIZED',{'decision':'approve','authorization_id':'r'}),
               (2,'RELEASE_COMPLETED',{'authorization_id':'wrong'})])
    assert not analyze(es)['release']['released']
    es[-1]['details']['authorization_id']='r'
    assert analyze(es)['release']['released']
    es.append(events([(3,'RELEASE_READINESS_EVALUATED',{'status':'BLOCKED'})])[0])
    assert not analyze(es)['release']['released']


def test_naive_and_invalid_timestamps_are_not_assumed_utc():
    assert stamp('2026-01-01T00:00:00') is None
    assert stamp(None) is None
    assert stamp('2026-01-01T00:00:00Z')==START.timestamp()


def test_malformed_optional_fields_do_not_create_release_or_crash():
    es=events([(0,'RELEASE_AUTHORIZED',{'decision':'approve','authorization_id':{}}),
               (1,'RELEASE_COMPLETED',{'authorization_id':[]}),
               (2,'MODEL_RESPONSE_RECORDED',{'stage':{}})])
    m=analyze(es)
    assert not m['release']['released']
    assert m['retry']['model_response_counts_by_stage']=={'unknown':1}
    with pytest.raises(ValueError):analyze(es,max_attempts='3')


def test_requirement_changes_invalidate_old_wait_boundaries():
    es=events([(0,'PLAN_CREATED',{}),(10,'REQUIREMENTS_CHANGED',{}),(20,'PLAN_APPROVED',{})])
    m=analyze(es)
    assert m['timing']['human_review_ready_to_decision_seconds']['value'] is None
    assert m['timing']['decisions_without_request_timestamp']==['audit:3']


def test_checkpoint_decoder_and_selection_keep_branches_and_threads_separate(tmp_path):
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from governance.reliability_metrics import read_checkpoints
    path=tmp_path/'workflow.db';kind,raw=JsonPlusSerializer().dumps_typed({'ts':START.isoformat(),'channel_values':{'__root__':{'status':'STOPPED_RELEASE_BLOCKED'}}})
    with sqlite3.connect(path) as conn:
        conn.execute('create table checkpoints(thread_id text,checkpoint_ns text,checkpoint_id text,type text,checkpoint blob)')
        conn.execute('insert into checkpoints values(?,?,?,?,?)',('w','nested','c',kind,raw))
        conn.execute('insert into checkpoints values(?,?,?,?,?)',('other','','d','pickle',b'not allowed'))
    before=path.read_bytes();cp=read_checkpoints(path)
    assert cp[0]['status']=='STOPPED_RELEASE_BLOCKED' and cp[1]['error']=='Unsupported checkpoint encoding'
    assert path.read_bytes()==before


def test_multiple_recoveries_mean_excludes_unresolved_incident():
    es=events([(0,'VALIDATION_FAILED',{}),(10,'VALIDATION_PASSED',{}),
               (20,'VALIDATION_FAILED',{}),(50,'VALIDATION_PASSED',{}),(60,'VALIDATION_FAILED',{})])
    m=analyze(es,cps('STOPPED',61))
    assert m['mttr_seconds']['value']==20
    assert len(m['recovery_intervals'])==2 and m['unrecovered_failure_event_id']==5
    assert m['engineering_validation']=='failed'


def test_empty_audit_reports_unknown_instead_of_false_success_or_zero_timing(tmp_path):
    path=database(tmp_path,[])
    result=report([{'audit_db':str(path),'workflow_id':'missing','cohort':'incomplete','provenance':'empty test database'}])
    c=result['cohorts']['incomplete'];m=result['workflows'][0]
    assert c['engineering_success_rate']['value'] is None and c['engineering_unknown_workflows']==1
    assert m['observed_window_seconds']['value'] is None
    assert m['end_to_end_latency_seconds']['value'] is None
    assert m['retry']['bound_check']['value'] is None


def test_cohorts_do_not_pool_controlled_fixtures_with_live_branches(tmp_path):
    es=events([(0,'VALIDATION_PASSED',{})]);other=events([(1,'VALIDATION_FAILED',{}),(2,'WORKFLOW_FINISHED',{'status':'STOPPED'})])
    for e in other:e['workflow_id']='other';e['id']+=1
    path=database(tmp_path,es+other)
    result=report([{'audit_db':str(path),'workflow_id':'w','cohort':'live','provenance':'selected live branch'},
                   {'audit_db':str(path),'workflow_id':'other','cohort':'fixture','provenance':'controlled failure'}])
    assert result['cohorts']['live']['engineering_success_rate']['value']==1
    assert result['cohorts']['fixture']['engineering_success_rate']['value']==0
    assert 'overall_success_rate' not in result


def test_policy_limit_violation_is_visible_without_resetting_attempt_ordinals():
    m=analyze(events([(0,'PARALLEL_ATTEMPT_STARTED',{'task_id':'a','attempt':4})]),max_attempts=3)
    assert m['retry']['bound_check']['value'] is False
    assert m['retry']['bound_check']['observed_max_attempt']==4


def test_stage_catalog_explicitly_marks_uninstrumented_stages_unavailable():
    es=events([(0,'STAGE_GATE_PASSED',{'stage':'design','gate':'entry'}),
               (5,'STAGE_GATE_PASSED',{'stage':'design','gate':'exit'}),
               (8,'TEST_RESULTS_APPROVED',{}),(18,'DOCUMENTATION_GATE_PASSED',{})])
    m=analyze(es)
    assert m['stage_durations_seconds']['design']['value']==5
    assert m['stage_durations_seconds']['documentation_boundary']['value']==10
    assert m['stage_durations_seconds']['requirements']['value'] is None
    assert m['timing']['agent_execution_wall_seconds']['value']==5


def test_mismatched_review_bundle_does_not_create_human_wait_sample():
    es=events([(10,'TEST_RESULTS_APPROVED',{'bundle_hash':'other'})])
    cp=cps('AWAITING_TEST_RESULTS_REVIEW',2);cp[0]['review_hash']='approved-bundle'
    m=analyze(es,cp)
    assert m['timing']['human_review_ready_to_decision_seconds']['value'] is None
    assert m['issues'][0]['issue'].startswith('Review hash mismatch')


def test_unanswered_review_is_unavailable_not_zero_wait():
    m=analyze(events([(0,'PLAN_CREATED',{})]))
    assert m['timing']['human_review_ready_to_decision_seconds']['value'] is None
    assert m['timing']['unclosed_review_stages']==['plan']
