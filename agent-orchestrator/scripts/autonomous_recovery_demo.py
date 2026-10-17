"""One workflow: controlled compiler defect, automatic rollback/replan, live correction.

The initial proposal is a recorded, previously validated live-model proposal with
one deliberate type-name typo. Recovery planning and correction use real Ollama;
all Maven invocations are real. Material replans pause for human approval.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

import live_multifile_demo as scenario
import live_implementation_demo as lifecycle
from agents import implementation_agent
from governance import audit_logger
from governance.task_orchestrator import tasks_from_plan, task_definition_hash
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from llm.provider import ModelResponse
import main

ROOT = Path(__file__).resolve().parents[2]


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=False)
    lifecycle.configure(directory)
    snapshot = directory / 'snapshot'
    snapshot.mkdir()
    shutil.copytree(ROOT/'url-shortener', snapshot/'url-shortener',
                    ignore=shutil.ignore_patterns('.git','target','.jdk-25','.env'))
    (snapshot/lifecycle.TEST_RELATIVE).write_text(lifecycle.TEST)
    for command in (('init',), ('add','.'), ('-c','user.name=Recovery Demo','-c','user.email=demo@example.com','commit','-m','Independent recovery baseline')):
        lifecycle.git(snapshot,*command)
    shutil.copytree(Path.home()/'.m2/repository', directory/'maven-cache', ignore=shutil.ignore_patterns('.locks'))
    metadata={'workflow_id':str(uuid.uuid4()), 'initial_proposal_source':'docs/evidence/live-status-api/success-proposal.json',
              'injection':'Change java.util.Map to nonexistent java.util.Mapp in service return type only'}
    workspace=lifecycle.initialize_workspace(metadata['workflow_id'],snapshot)
    metadata['workspace']=str(workspace)
    (directory/'run.json').write_text(json.dumps(metadata,indent=2))
    baseline=main.validate_implementation(workspace)
    (directory/'baseline.json').write_text(json.dumps(baseline,indent=2))
    if baseline['passed'] or 'UrlStatusAcceptanceTest' not in baseline['output']:
        raise RuntimeError('Baseline must demonstrate the missing status feature')
    plan=(ROOT/'docs/evidence/live-status-api/plan.json').read_text()
    tasks=tasks_from_plan(plan,lifecycle.RELATIVE,strict=True)
    (directory/'initial-plan.json').write_text(plan)
    recorded=json.loads((ROOT/metadata['initial_proposal_source']).read_text())
    service_edit=next(edit for edit in recorded['edits'] if edit['path']==lifecycle.RELATIVE)
    if 'java.util.Map' not in service_edit['new_text']:
        raise RuntimeError('Fixture does not have the controlled typo injection point')
    service_edit['new_text']=service_edit['new_text'].replace('java.util.Map','java.util.Mapp',1)
    (directory/'injected-proposal.json').write_text(json.dumps(recorded,indent=2))
    state={'workflow_id':metadata['workflow_id'],'workspace':str(workspace),'requirement':lifecycle.REQUIREMENT,
           'analysis':'Reuse the previously reviewed status API requirement. Independent acceptance tests are preinstalled.',
           'codebase_analysis':'Existing service/controller sources are in the isolated workspace.',
           'plan':plan,'tasks':tasks,'plan_version':1,'replan_history':[{'version':1,'source':'previously_reviewed_live_plan','plan':plan}],
           'approval':'','retry_count':0,'recovery_enabled':True,'attempt_history':[],
           'implementation_changed':False,'repair_applied':False}
    audit_logger.log_event(metadata['workflow_id'],'recovery_demo','WORKFLOW_STARTED',
                           {'source_plan_hash':task_definition_hash(tasks),'controlled_fault_fixture':True})
    config={'configurable':{'thread_id':metadata['workflow_id']}}
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        app.update_state(config,state,as_node='planning')
        result=app.invoke(None,config=config)
    assert '__interrupt__' in result
    print('INITIAL PLAN AWAITING APPROVAL:',directory/'initial-plan.json',flush=True)


def advance(directory,decision):
    lifecycle.configure(directory)
    metadata=json.loads((directory/'run.json').read_text())
    live=implementation_agent.model
    class DefectThenLiveProvider:
        def invoke(self,prompt,**kwargs):
            used=directory/'fault-injected.flag'
            if not used.exists():
                used.write_text('Controlled first-attempt fixture consumed\n')
                audit_logger.log_event(metadata['workflow_id'],'recovery_demo','CONTROLLED_MODEL_OUTPUT_FAULT_INJECTED',
                                       {'source':metadata['initial_proposal_source'],'defect':'java.util.Mapp'})
                return ModelResponse((directory/'injected-proposal.json').read_text())
            response=live.invoke(prompt,**kwargs)
            index=len(list(directory.glob('live-correction-*.json')))+1
            (directory/f'live-correction-{index}.json').write_text(response.text)
            (directory/f'live-correction-{index}-prompt.txt').write_text(prompt)
            audit_logger.log_event(metadata['workflow_id'],'recovery_demo','LIVE_CORRECTION_GENERATED',{'correction':index})
            return response
    implementation_agent.model=DefectThenLiveProvider()
    planner=main.model
    class PlanningRecorder:
        def invoke(self,prompt,**kwargs):
            index=len(list(directory.glob('recovery-plan-*.json')))+1
            (directory/f'recovery-plan-{index}-prompt.txt').write_text(prompt)
            response=planner.invoke(prompt,**kwargs)
            (directory/f'recovery-plan-{index}.json').write_text(response.text)
            return response
    main.model=PlanningRecorder()
    config={'configurable':{'thread_id':metadata['workflow_id']}}
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        pending=app.get_state(config)
        if not pending.next:
            raise RuntimeError('Workflow is already terminal')
        result=app.invoke(Command(resume=decision),config=config)
        final=app.get_state(config)
    summary={'workflow_id':metadata['workflow_id'],'status':result['status'],
             'retry_count':result.get('retry_count',0),'plan_version':result.get('plan_version',1),
             'validation':result.get('validation_result'), 'source_files_changed':result.get('source_files_changed',0),
             'failures':result.get('attempt_history',[]),
             'pending_approvals':[item.value for item in result.get('__interrupt__',[])],
             'next_nodes':list(final.next)}
    (directory/'result.json').write_text(json.dumps(summary,indent=2))
    if summary['pending_approvals']:
        audit_logger.log_event(metadata['workflow_id'],'recovery_demo','WORKFLOW_PAUSED_FOR_APPROVAL',
                               {'plan_version':summary['plan_version']})
    else:
        audit_logger.log_event(metadata['workflow_id'],'recovery_demo','WORKFLOW_FINISHED',
                               {'status':summary['status'],'retry_count':summary['retry_count']})
    print(json.dumps({key:value for key,value in summary.items() if key not in {'failures','validation'}},indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','advance'])
    parser.add_argument('directory',type=Path)
    parser.add_argument('--decision',choices=['approve','reject'])
    args=parser.parse_args()
    if args.action=='prepare': prepare(args.directory.resolve())
    else:
        if not args.decision: parser.error('advance requires an explicit approval decision')
        advance(args.directory.resolve(),args.decision)
