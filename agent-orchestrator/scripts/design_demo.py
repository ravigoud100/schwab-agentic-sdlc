"""Generate and gate a live architecture artifact; stop at design approval."""
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
from governance import audit_logger
from governance.design import assert_design_current
import main


def run(directory,source):
    directory.mkdir(parents=True,exist_ok=False)
    with sqlite3.connect(source/'workflow.db') as original,sqlite3.connect(directory/'workflow.db') as target:
        original.backup(target)
    with sqlite3.connect(source/'audit.db') as original,sqlite3.connect(directory/'audit.db') as target:
        original.backup(target)
    audit_logger.DB_PATH=directory/'audit.db'
    with sqlite3.connect(directory/'workflow.db') as conn:
        rows=conn.execute('select distinct thread_id from checkpoints').fetchall()
    assert len(rows)==1
    config={'configurable':{'thread_id':rows[0][0]}}
    real=main.model;attempts=[]
    class Recorder:
        def invoke(self,prompt,*,stage):
            assert stage=='design'
            number=len(attempts)+1
            (directory/f'attempt-{number}-prompt.txt').write_text(prompt)
            response=real.invoke(prompt,stage=stage)
            (directory/f'attempt-{number}-response.json').write_text(response.text)
            attempts.append(number)
            return response
    main.model=Recorder()
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        before=app.get_state(config).values
        workspace=Path(before['workspace'])
        def hashes():
            return {str(p.relative_to(workspace)):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (workspace/'url-shortener').rglob('*.java')}
        baseline=hashes()
        audit_logger.log_event(before['workflow_id'],'design_demo','DESIGN_ACCEPTANCE_STARTED',
            {'checkpoint_source':str(source),'requirements_hash':before['requirements_hash'],
             'approved_contract_hash':before['approved_contract_hash']})
        app.update_state(config,{'design_required':True,'design_artifact':{},'design_valid':False,
             'design_attempts':0,'design_errors':[],'design_history':[],'design_hash':'','approved_design_hash':'',
             'plan':'','tasks':[],'approval':'','approved_task_hash':'','approved_requirements_hash':'',
             'semantic_valid':False,'semantic_plan_hash':''},as_node='contract_review')
        result=app.invoke(None,config=config)
        snapshot=app.get_state(config)
        assert hashes()==baseline
        assert result['requirements_hash']==before['requirements_hash']
        assert result['approved_contract_hash']==before['approved_contract_hash']
        summary={key:result.get(key) for key in ('workflow_id','workspace','status','requirement_version',
            'requirements_hash','approved_contract_hash','clarification_answers','design_artifact','design_hash',
            'approved_design_hash','design_valid','design_attempts','design_errors','design_history')}
        summary.update({'thread_id':rows[0][0],'next_nodes':list(snapshot.next),'live_calls':len(attempts),
                        'java_files_changed':0,'java_sha256':baseline,'source_checkpoint':str(source)})
        (directory/'result.json').write_text(json.dumps(summary,indent=2))
        if result.get('design_valid'):
            assert_design_current(result)
            assert snapshot.next==('design_review',)
            (directory/'design.json').write_text(json.dumps(result['design_artifact'],indent=2))
        print(json.dumps({k:v for k,v in summary.items() if k not in ('java_sha256','design_artifact','design_history')},indent=2),flush=True)
    with sqlite3.connect(directory/'audit.db') as conn:
        events=[{'workflow_id':w,'timestamp':t,'agent':a,'event':e,'details':json.loads(d)}
                for w,t,a,e,d in conn.execute('SELECT workflow_id,timestamp,agent,event,details FROM audit_events ORDER BY id')]
    (directory/'audit.json').write_text(json.dumps(events,indent=2))
    assert not (workspace/'.agent-transactions').exists()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args()
    run(args.directory.resolve(),args.source.resolve())
