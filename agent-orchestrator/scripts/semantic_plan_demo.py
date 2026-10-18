"""Controlled generic-plan injection followed by real, bounded Ollama replanning.

Uses a new checkpoint DB containing the previous live clarified requirement state.
Contract approval must be explicitly supplied by the reviewed human decision.
Stops at plan approval; never applies edits.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

os.environ['LLM_PROVIDER']='ollama'
os.environ['OLLAMA_FORMAT']='json'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.semantic_plan import custom_expiration_contract
from llm.provider import ModelResponse
import main


def run(directory, source, approve_contract):
    if not approve_contract:
        raise ValueError('Explicit human contract approval is required')
    directory.mkdir(parents=True, exist_ok=False)
    metadata=json.loads((source/'run.json').read_text())
    config={'configurable':{'thread_id':metadata['workflow_id']}}
    # Fork the database for evidence isolation; retain the real clarified state and thread.
    with sqlite3.connect(source/'workflow.db') as original, sqlite3.connect(directory/'workflow.db') as target:
        original.backup(target)
    with sqlite3.connect(source/'audit.db') as original, sqlite3.connect(directory/'audit.db') as target:
        original.backup(target)
    audit_logger.DB_PATH=directory/'audit.db'
    real=main.model
    calls=[]
    class Recorder:
        def invoke(self,prompt,*,stage):
            assert stage=='planning'
            number=len(calls)+1
            (directory/f'attempt-{number}-prompt.txt').write_text(prompt)
            if number==1:
                response=ModelResponse(json.dumps({'tasks':[{'task_id':'generic','description':'Implement URL expiration functionality.',
                     'dependencies':[],'execution_type':'implementation','allowed_files':[main.IMPLEMENTATION_RELATIVE_PATH]}]}))
                origin='deliberately_generic_fixture'
            else:
                response=real.invoke(prompt,stage=stage)
                origin='live_ollama'
            (directory/f'attempt-{number}-response.json').write_text(response.text)
            calls.append({'attempt':number,'origin':origin})
            return response
    main.model=Recorder()
    contract=custom_expiration_contract()
    (directory/'contract.json').write_text(json.dumps(contract,indent=2))
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=main.workflow.compile(checkpointer=saver)
        before=app.get_state(config).values
        (directory/'before.json').write_text(json.dumps({'requirement_version':before['requirement_version'],
            'requirements_hash':before['requirements_hash'],'clarification_answers':before['clarification_answers']},indent=2))
        audit_logger.log_event(metadata['workflow_id'],'semantic_plan_demo','SEMANTIC_ACCEPTANCE_STARTED',
            {'controlled_first_proposal':True,'human_contract_approved':True,'source_checkpoint_directory':str(source)})
        app.update_state(config,{'obligation_contract':contract,'approved_task_hash':'','approval':'',
                               'semantic_attempts':0,'semantic_errors':[],'semantic_history':[],'semantic_valid':False},as_node='codebase')
        paused=app.invoke(None,config=config)
        assert paused['__interrupt__'][0].value['type']=='REQUIREMENT_CONTRACT_APPROVAL'
        result=app.invoke(Command(resume='approve'),config=config)
        state=app.get_state(config)
        assert result['requirements_hash']==before['requirements_hash']
        assert result['requirement_version']==before['requirement_version']
        summary={key:result.get(key) for key in ('workflow_id','status','requirement_version','requirements_hash',
            'clarification_answers','obligation_contract','approved_contract_hash','semantic_valid','semantic_attempts',
            'semantic_errors','semantic_history','semantic_plan_hash','plan','plan_requirements_hash')}
        summary.update({'next_nodes':list(state.next),'attempts':calls,'thread_id':config['configurable']['thread_id']})
        (directory/'result.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary,indent=2),flush=True)
        if result.get('plan'):(directory/'plan.json').write_text(result['plan'])
    with sqlite3.connect(directory/'audit.db') as conn:
        events=[{'workflow_id':w,'timestamp':t,'agent':a,'event':e,'details':json.loads(d)}
                for w,t,a,e,d in conn.execute('SELECT workflow_id,timestamp,agent,event,details FROM audit_events ORDER BY id')]
    (directory/'audit.json').write_text(json.dumps(events,indent=2))
    assert not (Path(metadata['workspace'])/'.agent-transactions').exists()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--approved-contract',action='store_true')
    args=parser.parse_args()
    run(args.directory.resolve(),args.source.resolve(),args.approved_contract)
