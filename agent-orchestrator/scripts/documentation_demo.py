"""Resume verified test evidence through human review, docs, and blocked readiness."""
import argparse,json,os,sqlite3,sys,uuid
from pathlib import Path
os.environ['LLM_PROVIDER']='ollama';os.environ['OLLAMA_FORMAT']='json'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.test_stage import build_test_workflow
from governance.release_evidence import verify
from governance.semantic_plan import digest
from governance.documentation_release import build_workflow,current
from llm.provider import get_model


def run(directory,decision=None,retry=False):
    audit_logger.DB_PATH=directory/'audit.db'
    if decision is None and not retry:
        directory.mkdir(parents=True,exist_ok=False)
        with SqliteSaver.from_conn_string('/private/tmp/schwab-test-generation/workflow.db') as saver:
            origin=build_test_workflow(None).compile(checkpointer=saver).get_state({'configurable':{'thread_id':'11e9ef18-a360-43dc-a455-069ba1f7a8d4'}}).values
        origin['origin_audit_db']='/private/tmp/schwab-test-generation/audit.db'
        bundle=verify(origin);run_id=str(uuid.uuid4())
        (directory/'run.json').write_text(json.dumps({'run_id':run_id,'bundle_hash':digest(bundle)},indent=2))
        (directory/'bundle.json').write_text(json.dumps(bundle,indent=2))
        initial={'origin':origin,'bundle_hash':digest(bundle),'run_id':run_id,'output_dir':str(directory)}
    else:
        metadata=json.loads((directory/'run.json').read_text());run_id=metadata['run_id']
    config={'configurable':{'thread_id':run_id}}
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=build_workflow(get_model()).compile(checkpointer=saver)
        if retry:
            state=app.get_state(config).values
            current(state)
            assert state['test_review_hash']==state['bundle_hash']
            assert state['status'] in ('STOPPED_DOCUMENTATION_INVALID','DOCUMENTATION_RETRY')
            attempts=state.get('documentation_attempts',1)
            if attempts>=3:raise PermissionError('Documentation retry budget exhausted')
            audit_logger.log_event(run_id,'documentation_release','DOCUMENTATION_RETRY_REQUESTED',
                                   {'bundle_hash':state['bundle_hash'],'completed_attempts':attempts})
            app.update_state(config,{**state,'status':'TEST_RESULTS_APPROVED','documentation_attempts':attempts,
                'documentation_errors':[state.get('error','Invalid documentation output')]},as_node='test_results_review')
            result=app.invoke(None,config=config)
        elif decision is None:result=app.invoke(initial,config=config)
        else:
            assert app.get_state(config).next==('test_results_review',)
            result=app.invoke(Command(resume=decision),config=config)
        state=app.get_state(config)
        summary={key:result.get(key) for key in ('status','bundle_hash','test_review_hash','document','readiness','error')}
        summary.update({'run_id':run_id,'next_nodes':list(state.next)})
        (directory/'result.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2),flush=True)
    if (directory/'audit.db').exists():
        with sqlite3.connect(directory/'audit.db') as c:
            events=[{'workflow_id':w,'timestamp':t,'agent':a,'event':e,'details':json.loads(d)} for w,t,a,e,d in c.execute('select workflow_id,timestamp,agent,event,details from audit_events order by id')]
        (directory/'audit.json').write_text(json.dumps(events,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('--decision',choices=['approve','reject'])
    parser.add_argument('--retry',action='store_true')
    args=parser.parse_args();run(args.directory.resolve(),args.decision,args.retry)
