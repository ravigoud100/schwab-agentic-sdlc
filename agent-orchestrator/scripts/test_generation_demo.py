"""Explicitly approved, isolated Clock/test-stage acceptance run."""
import argparse,json,os,shutil,sqlite3,subprocess,sys,uuid
from pathlib import Path
os.environ['LLM_PROVIDER']='ollama';os.environ['OLLAMA_FORMAT']='json'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.test_stage import build_test_workflow
from governance.workspace_manager import initialize_workspace
from llm.provider import get_model


def run(directory,approved):
    if not approved:raise PermissionError('Human review of Clock patch and test scope is required')
    root=Path(__file__).resolve().parents[2];directory.mkdir(parents=True,exist_ok=False)
    with SqliteSaver.from_conn_string('/private/tmp/schwab-design-correction/workflow.db') as saver:
        import main
        context=main.workflow.compile(checkpointer=saver).get_state({'configurable':{'thread_id':'d3ea55e8-4661-4a8d-a370-2c14ad42f5ac'}}).values
    repo=directory/'snapshot';repo.mkdir()
    shutil.copytree(Path(context['workspace'])/'url-shortener',repo/'url-shortener',ignore=shutil.ignore_patterns('target','.git','.jdk-25'))
    for args in [('init',),('add','.'),('-c','user.name=Test Stage Demo','-c','user.email=demo@example.com','commit','-m','Reviewed unchanged baseline')]:
        subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True)
    workflow_id=str(uuid.uuid4());workspace=initialize_workspace(workflow_id,repo)
    context={**context,'parent_workflow_id':context['workflow_id'],'workflow_id':workflow_id,'workspace':str(workspace)}
    scope=json.loads((root/'docs/evidence/test-generation/scope.json').read_text())
    original=(workspace/scope['production_file']).read_bytes()
    # Materialize only the exact human-reviewed diff; no live implementation generation.
    temporary=directory/'reviewed-patch';temporary.mkdir()
    (temporary/'url-shortener/src/main/java/url_shortener').mkdir(parents=True)
    (temporary/scope['production_file']).write_bytes(original)
    subprocess.run(['git','apply',str(root/'docs/evidence/test-generation/clock-seam.diff')],cwd=temporary,check=True)
    revised=(temporary/scope['production_file']).read_bytes()
    cache=directory/'maven-cache'
    shutil.copytree(Path.home()/'.m2/repository',cache,ignore=shutil.ignore_patterns('.locks'))
    evidence=directory/'evidence';evidence.mkdir()
    audit_logger.DB_PATH=directory/'audit.db'
    config={'configurable':{'thread_id':workflow_id}}
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=build_test_workflow(get_model()).compile(checkpointer=saver)
        initial={'context':context,'scope':scope,'old_source':original,'new_source':revised,
                 'evidence':str(evidence),'cache':str(cache)}
        paused=app.invoke(initial,config=config)
        assert paused['__interrupt__'][0].value['type']=='TEST_STAGE_APPROVAL'
        result=app.invoke(Command(resume='approve'),config=config)
        summary={key:result.get(key) for key in ('status','validation','negative_validation','error','recovery',
                 'approved_scope_hash','baseline_hash','test_hash','source_transaction')}
        summary.update({'workflow_id':workflow_id,'workspace':str(workspace),'thread_id':workflow_id,
            'next_nodes':list(app.get_state(config).next),'requirements_hash':context['requirements_hash'],
            'approved_design_hash':context['approved_design_hash'],'architecture_amendment':scope,
            'scope_approval':'Explicit user approval of exact reviewed Clock patch and isolated test scope'})
        (directory/'result.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2),flush=True)
    with sqlite3.connect(directory/'audit.db') as c:
        events=[{'workflow_id':w,'timestamp':t,'agent':a,'event':e,'details':json.loads(d)} for w,t,a,e,d in c.execute('select workflow_id,timestamp,agent,event,details from audit_events order by id')]
    (directory/'audit.json').write_text(json.dumps(events,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    parser.add_argument('--approved-scope',action='store_true');args=parser.parse_args()
    run(args.directory.resolve(),args.approved_scope)
