"""Prepare/resume a bounded sequential demo; every human gate stays explicit.

Imports previously approved requirements, proposes a Clock design amendment and an
assistant-authored plan. Production edits, constrained test specs and fact selection
use the real provider. No historical source patch is applied.
"""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid
os.environ['LLM_PROVIDER']='ollama';os.environ['OLLAMA_FORMAT']='json'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.audit_logger import log_event
from governance.design import validate_design,design_binding
from governance.semantic_plan import digest
from governance.workspace_manager import initialize_workspace
from governance.plan_lineage import before_execution
from llm.provider import get_model,ModelResponse
import main

PARENT='d3ea55e8-4661-4a8d-a370-2c14ad42f5ac'
SERVICE='url-shortener/src/main/java/url_shortener/UrlService.java'
TEST='url-shortener/src/test/java/url_shortener/UrlExpirationIntegrationTests.java'
NEW='url-shortener/src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java'


def dump(path,value):
    path.write_text(json.dumps(value,indent=2,default=lambda v:{'base64':base64.b64encode(v).decode()} if isinstance(v,bytes) else str(v))+'\n')


def prepare(directory,source):
    directory.mkdir(parents=True,exist_ok=False)
    with SqliteSaver.from_conn_string(str(source/'workflow.db')) as saver:
        parent=main.workflow.compile(checkpointer=saver).get_state({'configurable':{'thread_id':PARENT}}).values
    snapshot=directory/'snapshot';snapshot.mkdir()
    shutil.copytree(Path(parent['workspace'])/'url-shortener',snapshot/'url-shortener',ignore=shutil.ignore_patterns('target','.git','.jdk-25'))
    for args in [('init',),('add','.'),('-c','user.name=SDLC Demo','-c','user.email=demo@example.com','commit','-m','Unmodified engineering baseline')]:
        subprocess.run(['git',*args],cwd=snapshot,check=True,capture_output=True)
    wid=str(uuid.uuid4());workspace=initialize_workspace(wid,snapshot)
    evidence=directory/'evidence';evidence.mkdir()
    c={**parent,'workflow_id':wid,'parent_workflow_id':PARENT,'workspace':str(workspace),
        'plan_lineage_required':True,'integrated_sdlc':True,'integration_evidence':str(evidence),
        'integration_cache':'/private/tmp/schwab-test-generation/maven-cache',
        'plan':'','tasks':[],'approval':'','approved_task_hash':'','plan_authorization':{},
        'approved_design_hash':'','semantic_valid':False,'semantic_plan_hash':'','plan_design_hash':'',
        'implementation_transaction':{},'integrated_result':{},'active_task_ids':[],
        'design_version':parent.get('design_version',2)+1,'design_feedback':'','design_errors':[],
        'status':'AWAITING_DESIGN_APPROVAL'}
    design=copy.deepcopy(parent['design_artifact'])
    design['architectural_decisions'].append({'id':'clock-seam','decision':
        'Keep the public UrlService(UrlRepository) constructor, annotate it with @org.springframework.beans.factory.annotation.Autowired, and delegate to this(repository, java.time.Clock.systemUTC()). Add public UrlService(UrlRepository, java.time.Clock), store a private final Clock, and use java.time.Instant.now(clock) for both creation validation and redirect expiration checks. Use fixed UTC in additive GeneratedExpirationBoundaryTest; existing tests and build remain unchanged.',
        'rationale':'Deterministic equality-boundary testing without altering HTTP 201/400/404/410, optional expiration, redirects, or click behavior.'})
    design['compatibility_risks'].append({'id':'spring-clock-construction','description':'Adding a second constructor can make Spring construction ambiguous.',
        'mitigation':'Annotate the existing repository-only constructor with Autowired; production defaults to Clock.systemUTC(), tests pass Clock.fixed explicitly.'})
    for component in design['components']:
        if component['path']==SERVICE:component['responsibility']+=' Use one injected Clock for creation and access checks; retain production Spring construction.'
    c['design_artifact']=design;c['design_valid']=True;c['design_hash']=design_binding(c)
    validate_design(design,c)
    task={'task_id':'clock-boundary-seam','description':
        'Implement only the approved Clock seam in UrlService: private final java.time.Clock; retain and annotate the repository-only public constructor with fully qualified @Autowired and delegate to Clock.systemUTC(); add a public repository/Clock constructor assigning both fields. Replace both creation and access Instant.now() calls with Instant.now(clock). Preserve strictly-future optional caller-provided UTC expiration and HTTP 400 for past/equal-now; preserve null expiration, HTTP 201/404/410, redirects and clicks. Controller/entity behavior already exists and needs no edit. Existing integration tests are read-only coverage references. The separately approved additive test stage renders future/past/equal-now/missing assertions with a fixed UTC clock; no existing tests/build modifications.',
        'dependencies':[],'execution_type':'implementation','allowed_files':[SERVICE,TEST]}
    plan={'tasks':[task],'coverage':[{'obligation_id':o['id'],'kind':o['kind'],'spec':o['spec'],
        'task_id':task['task_id'],'files':[TEST if o['kind']=='test' else SERVICE]} for o in c['obligation_contract']['obligations']],
        'test_stage':{'allowed_new_test_files':[NEW],'protected_existing_test_files':[TEST]}}
    dump(directory/'proposed-design.json',design);dump(directory/'proposed-plan.json',plan)
    dump(directory/'run.json',{'workflow_id':wid,'parent_workflow_id':PARENT,'workspace':str(workspace),
        'source_checkpoint':str(source),'design_hash':c['design_hash'],'plan_hash':digest(json.dumps(plan))})
    audit_logger.DB_PATH=directory/'audit.db'
    log_event(wid,'integrated_demo','APPROVED_REQUIREMENT_CONTEXT_IMPORTED',{'parent_workflow_id':PARENT,
        'requirements_hash':c['requirements_hash'],'contract_hash':c['approved_contract_hash'],
        'source_checkpoint':str(source),'plan_origin':'assistant-authored proposed plan, not live planning'})
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver);config={'configurable':{'thread_id':wid}}
        app.update_state(config,c,as_node='design')
        result=app.invoke(None,config)
        export(directory,app,config,result)


class RecordedProvider:
    def __init__(self,directory):self.directory=directory;self.provider=get_model()
    def invoke(self,prompt,*,stage=None):
        if stage=='planning':
            raw=json.dumps(json.loads((self.directory/'proposed-plan.json').read_text()))
            origin='assistant-authored reviewed-plan fixture'
        else:
            response=self.provider.invoke(prompt,stage=stage)
            raw=response.text;origin='live local Ollama'
        index=1
        while (self.directory/f'{stage}-{index}-response.txt').exists():index+=1
        (self.directory/f'{stage}-{index}-prompt.txt').write_text(prompt)
        (self.directory/f'{stage}-{index}-response.txt').write_text(raw)
        if stage!='planning':dump(self.directory/f'{stage}-{index}-metadata.json',response.metadata)
        log_event(json.loads((self.directory/'run.json').read_text())['workflow_id'],'integrated_demo','MODEL_RESPONSE_RECORDED',
            {'stage':stage,'origin':origin,'response_hash':digest(raw)})
        return ModelResponse(raw)


def export(directory,app,config,result):
    snap=app.get_state(config)
    child={}
    if snap.next==('integrated_sdlc',):
        # Inspect the persisted subgraph state; the parent node has not returned
        # while a nested interrupt is pending, so its status is intentionally old.
        with sqlite3.connect(directory/'workflow.db') as conn:
            namespaces=[r[0] for r in conn.execute("select distinct checkpoint_ns from checkpoints where checkpoint_ns like 'integrated_sdlc:%'")]
        with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
            checkpoints=[saver.get_tuple({'configurable':{**config['configurable'],'checkpoint_ns':ns}}) for ns in namespaces]
        if checkpoints:
            latest=max((item for item in checkpoints if item),key=lambda item:item.checkpoint['ts'])
            child=latest.checkpoint['channel_values'].get('__root__',{})
    status=child.get('status',result.get('status'))
    dump(directory/'result.json',{'status':status,'engineering_status':result.get('status'),'next_nodes':list(snap.next),
        'interrupts':[i.value for task in snap.tasks for i in task.interrupts],
        'integrated_result':child or result.get('integrated_result',{})})
    with sqlite3.connect(directory/'audit.db') as c:
        rows=[{'id':i,'workflow_id':w,'event':e,'details':json.loads(d)} for i,w,e,d in c.execute('select id,workflow_id,event,details from audit_events order by id')]
    dump(directory/'audit.json',rows)
    print(json.dumps({'status':status,'next':list(snap.next),'review_file':str(directory/'result.json')},indent=2),flush=True)


def resume(directory,decision):
    metadata=json.loads((directory/'run.json').read_text());audit_logger.DB_PATH=directory/'audit.db'
    main.model=RecordedProvider(directory)
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver);config={'configurable':{'thread_id':metadata['workflow_id']}}
        snap=app.get_state(config)
        if not snap.next:raise PermissionError('Workflow has terminated; no decision can be applied')
        result=app.invoke(Command(resume=decision),config);export(directory,app,config,result)


def retry_infrastructure(directory):
    """Explicit harness retry of a permission-blocked call with zero source writes.

    This is not autonomous model correction and cannot alter the approved plan.
    Retain the failed evidence directory and use a new one on the same thread.
    """
    metadata=json.loads((directory/'run.json').read_text());audit_logger.DB_PATH=directory/'audit.db'
    main.model=RecordedProvider(directory)
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver);config={'configurable':{'thread_id':metadata['workflow_id']}}
        snapshot=app.get_state(config);state=snapshot.values;failed=state.get('integrated_result',{})
        if snapshot.next or failed.get('recovery')!='no_source_writes' or failed.get('transaction') or failed.get('error')!='[Errno 1] Operation not permitted':
            raise PermissionError('Only a terminated, permission-blocked, zero-write attempt may use this retry')
        with sqlite3.connect(directory/'audit.db') as conn:
            count=conn.execute("select count(*) from audit_events where event='INTEGRATION_INFRASTRUCTURE_RETRY'").fetchone()[0]
        if count>=2:raise PermissionError('Infrastructure retry budget exhausted')
        task={**state['tasks'][0],'status':'READY'}
        before_execution({**state,'tasks':[task],'active_task_ids':[task['task_id']]},task)
        evidence=directory/f'evidence-infrastructure-retry-{count+1}';evidence.mkdir()
        log_event(state['workflow_id'],'integrated_demo','INTEGRATION_INFRASTRUCTURE_RETRY',
            {'unchanged_plan_hash':digest(state['plan']),'previous_evidence':state['integration_evidence'],
             'evidence':str(evidence),'source_writes':0})
        app.update_state(config,{'integration_evidence':str(evidence)},as_node='approved')
        result=app.invoke(None,config);export(directory,app,config,result)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    parser.add_argument('--source',type=Path,default=Path('/private/tmp/schwab-design-correction'))
    parser.add_argument('--decision',choices=['approve','reject'])
    parser.add_argument('--retry-infrastructure',action='store_true')
    args=parser.parse_args()
    if args.retry_infrastructure:retry_infrastructure(args.directory.resolve())
    elif args.decision:resume(args.directory.resolve(),args.decision)
    else:prepare(args.directory.resolve(),args.source.resolve())
