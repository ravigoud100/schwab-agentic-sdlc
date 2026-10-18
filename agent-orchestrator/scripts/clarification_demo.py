"""Live requirements/clarification demo; stops at plan review without source writes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid

os.environ['LLM_PROVIDER']='ollama'
os.environ['OLLAMA_FORMAT']='json'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.workspace_manager import initialize_workspace
import main

REQUIREMENT=(
    'Enhance the existing URL shortener with optional URL expiration. Preserve existing links, '
    'HTTP 410 for expired URLs, and existing active-link redirect behavior. '
    'There is exactly one deliberately unresolved configuration decision for this demonstration: '
    'use a fixed 24-hour TTL computed from link creation, OR let each caller provide a future UTC '
    'expiration timestamp. Neither mode is approved yet. Ask the human to choose between those two '
    'modes before codebase analysis or planning. Do not select the suggested default automatically. '
    'No code changes may execute until the later engineering plan approval.'
)


def git(repo,*args):
    import subprocess
    subprocess.run(['git',*args],cwd=repo,check=True,capture_output=True,text=True)


def configure(directory):
    audit_logger.DB_PATH=directory/'audit.db'
    provider=main.model
    class Recorder:
        def invoke(self,prompt,**kwargs):
            response=provider.invoke(prompt,**kwargs)
            stage=kwargs['stage']
            (directory/f'{stage}-prompt.txt').write_text(prompt)
            (directory/f'{stage}-response.txt').write_text(response.text)
            return response
    main.model=Recorder()


def export(directory,result,next_nodes):
    summary={key:result.get(key) for key in ('workflow_id','status','requirement','requirement_version',
              'structured_requirements','requirements_hash','requirements_source_hash','requirements_valid',
              'clarification_answers','clarification_complete','clarification_history','analysis',
              'codebase_analysis','plan','plan_requirements_hash','plan_requirement_version')}
    summary['pending_approvals']=[item.value for item in result.get('__interrupt__',[])]
    summary['next_nodes']=list(next_nodes)
    (directory/'result.json').write_text(json.dumps(summary,indent=2))
    with sqlite3.connect(directory/'audit.db') as conn:
        events=[{'workflow_id':workflow,'timestamp':stamp,'agent':agent,'event':event,'details':json.loads(details)}
                for workflow,stamp,agent,event,details in conn.execute('SELECT workflow_id,timestamp,agent,event,details FROM audit_events ORDER BY id')]
    (directory/'audit.json').write_text(json.dumps(events,indent=2))
    print(json.dumps({'workflow_id':summary['workflow_id'],'status':summary['status'],
                     'pending_approvals':summary['pending_approvals'],'next_nodes':summary['next_nodes']},indent=2),flush=True)


def prepare(directory):
    directory.mkdir(parents=True,exist_ok=False)
    configure(directory)
    root=Path(__file__).resolve().parents[2]
    repo=directory/'snapshot'
    repo.mkdir()
    shutil.copytree(root/'url-shortener',repo/'url-shortener',ignore=shutil.ignore_patterns('.git','target','.jdk-25','.env'))
    git(repo,'init');git(repo,'add','.')
    git(repo,'-c','user.name=Clarification Demo','-c','user.email=demo@example.com','commit','-m','Unmodified Java baseline')
    workflow_id=str(uuid.uuid4())
    workspace=initialize_workspace(workflow_id,repo)
    metadata={'workflow_id':workflow_id,'workspace':str(workspace)}
    (directory/'run.json').write_text(json.dumps(metadata,indent=2))
    audit_logger.log_event(workflow_id,'clarification_demo','WORKFLOW_STARTED',{'requirement':REQUIREMENT})
    config={'configurable':{'thread_id':workflow_id}}
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        result=app.invoke({**metadata,'requirement':REQUIREMENT,'requirement_version':1,'approval':'',
                           'retry_count':0,'recovery_enabled':True},config=config)
        export(directory,result,app.get_state(config).next)
    if not result.get('__interrupt__') or result['__interrupt__'][0].value.get('type')!='REQUIREMENT_CLARIFICATION':
        raise RuntimeError('Demo must pause for unresolved requirement clarification')
    assert not result.get('plan')


def answer(directory,answers_file,decision):
    configure(directory)
    metadata=json.loads((directory/'run.json').read_text())
    config={'configurable':{'thread_id':metadata['workflow_id']}}
    answers=json.loads(answers_file.read_text()) if answers_file else {}
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        before=app.get_state(config)
        if before.next!=('clarification',): raise RuntimeError('Only answer an active clarification interrupt')
        result=app.invoke(Command(resume={'decision':decision,'answers':answers}),config=config)
        export(directory,result,app.get_state(config).next)
    if result.get('plan'):
        (directory/'plan.json').write_text(result['plan'])
        assert result['plan_requirements_hash']==result['requirements_hash']
    # This runner never resumes the engineering plan approval.
    assert not (Path(metadata['workspace'])/'.agent-transactions').exists()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','answer'])
    parser.add_argument('directory',type=Path)
    parser.add_argument('--answers-file',type=Path)
    parser.add_argument('--decision',choices=['approve','reject'],default='approve')
    args=parser.parse_args()
    if args.action=='prepare':prepare(args.directory.resolve())
    else:answer(args.directory.resolve(),args.answers_file,args.decision)
