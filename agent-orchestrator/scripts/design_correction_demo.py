"""Resume human design corrections on a retained checkpoint; never approve the revision."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
os.environ['LLM_PROVIDER']='ollama'
os.environ['OLLAMA_FORMAT']='json'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.design import assert_design_current
import main

FEEDBACK=('Correct the stale compatibility risk claiming the expiration configuration decision has not been approved. '
          'A1 is already human-approved: optional caller-provided future UTC expiration timestamps, preserving missing-expiration behavior. '
          'Replace that obsolete risk with an actual compatibility risk and mitigation, and explicitly acknowledge the approved decision '
          'in the architectural rationale. Preserve all eight approved obligations and valid production/test component references. '
          'Do not change requirements, the approved contract, or Java source.')


def run(directory,source):
    directory.mkdir(parents=True,exist_ok=False)
    for name in ('workflow.db','audit.db'):
        with sqlite3.connect(source/name) as original,sqlite3.connect(directory/name) as target:original.backup(target)
    audit_logger.DB_PATH=directory/'audit.db'
    with sqlite3.connect(directory/'workflow.db') as conn:threads=conn.execute('select distinct thread_id from checkpoints').fetchall()
    assert len(threads)==1
    config={'configurable':{'thread_id':threads[0][0]}}
    real=main.model;calls=[]
    class Recorder:
        def invoke(self,prompt,*,stage):
            assert stage=='design', 'Planning must remain blocked until explicit revised-design approval'
            n=len(calls)+1
            (directory/f'attempt-{n}-prompt.txt').write_text(prompt)
            response=real.invoke(prompt,stage=stage)
            (directory/f'attempt-{n}-response.json').write_text(response.text);calls.append(n)
            return response
    main.model=Recorder()
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver);before=app.get_state(config)
        assert before.next==('design_review',)
        workspace=Path(before.values['workspace'])
        def hashes():return {str(p.relative_to(workspace)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (workspace/'url-shortener').rglob('*.java')}
        original_hashes=hashes()
        (directory/'original-design.json').write_text(json.dumps(before.values['design_artifact'],indent=2))
        (directory/'feedback.json').write_text(json.dumps({'decision':'request_changes','feedback':FEEDBACK},indent=2))
        audit_logger.log_event(before.values['workflow_id'],'design_correction_demo','DESIGN_CORRECTION_STARTED',
                              {'source':str(source),'old_design_hash':before.values['design_hash']})
        result=app.invoke(Command(resume={'decision':'request_changes','feedback':FEEDBACK}),config=config)
        snapshot=app.get_state(config)
        for key in ('requirements_hash','requirement_version','approved_contract_hash','clarification_answers'):
            assert result[key]==before.values[key]
        assert hashes()==original_hashes and not result.get('approved_design_hash') and not result.get('plan')
        summary={key:result.get(key) for key in ('workflow_id','status','requirement_version','requirements_hash',
            'approved_contract_hash','clarification_answers','design_artifact','design_valid','design_version','design_hash',
            'approved_design_hash','design_versions','design_feedback','design_history','design_review_history','design_errors')}
        summary.update({'old_design_hash':before.values['design_hash'],'thread_id':threads[0][0],
                        'next_nodes':list(snapshot.next),'java_files_changed':0,'java_sha256':original_hashes,'live_calls':len(calls)})
        if result.get('design_valid'):
            assert_design_current(result)
            assert result['design_hash']!=before.values['design_hash'] and snapshot.next==('design_review',)
            (directory/'revised-design.json').write_text(json.dumps(result['design_artifact'],indent=2))
        (directory/'result.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps({k:v for k,v in summary.items() if k not in ('design_artifact','design_versions','design_history','java_sha256')},indent=2),flush=True)
    with sqlite3.connect(directory/'audit.db') as conn:
        events=[{'workflow_id':w,'timestamp':t,'agent':a,'event':e,'details':json.loads(d)} for w,t,a,e,d in conn.execute('SELECT workflow_id,timestamp,agent,event,details FROM audit_events ORDER BY id')]
    (directory/'audit.json').write_text(json.dumps(events,indent=2))


def review(directory,decision):
    """Call only after explicit human review of revised-design.json."""
    audit_logger.DB_PATH=directory/'audit.db'
    metadata=json.loads((directory/'result.json').read_text())
    config={'configurable':{'thread_id':metadata['thread_id']}}
    real=main.model;calls=[]
    class Recorder:
        def invoke(self,prompt,*,stage):
            assert stage=='planning', 'Design approval must not authorize source edits'
            n=len(calls)+1
            (directory/f'planning-{n}-prompt.txt').write_text(prompt)
            response=real.invoke(prompt,stage=stage)
            (directory/f'planning-{n}-response.json').write_text(response.text);calls.append(n)
            return response
    main.model=Recorder()
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver);before=app.get_state(config)
        assert before.next==('design_review',) and before.values['design_hash']==metadata['design_hash']
        result=app.invoke(Command(resume=decision),config=config);snapshot=app.get_state(config)
        summary={key:result.get(key) for key in ('workflow_id','status','requirement_version','requirements_hash',
            'approved_contract_hash','design_version','design_hash','approved_design_hash','plan_design_hash',
            'plan','plan_requirements_hash','approved_task_hash','semantic_valid','semantic_history','semantic_errors')}
        summary.update({'thread_id':metadata['thread_id'],'next_nodes':list(snapshot.next),'planning_calls':len(calls)})
        for path,expected in metadata['java_sha256'].items():
            assert hashlib.sha256((Path(before.values['workspace'])/path).read_bytes()).hexdigest()==expected
        assert not result.get('approved_task_hash')
        if result.get('plan'):
            (directory/'plan.json').write_text(result['plan'])
            assert snapshot.next==('approval',)
        (directory/'planning-result.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary,indent=2),flush=True)
    with sqlite3.connect(directory/'audit.db') as conn:
        events=[{'workflow_id':w,'timestamp':t,'agent':a,'event':e,'details':json.loads(d)}
                for w,t,a,e,d in conn.execute('SELECT workflow_id,timestamp,agent,event,details FROM audit_events ORDER BY id')]
    (directory/'planning-audit.json').write_text(json.dumps(events,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('--source',type=Path)
    parser.add_argument('--decision',choices=['approve','reject'])
    args=parser.parse_args()
    if args.decision:review(args.directory.resolve(),args.decision)
    elif args.source:run(args.directory.resolve(),args.source.resolve())
    else:parser.error('--source is required for generation; --decision requires prior human review')
