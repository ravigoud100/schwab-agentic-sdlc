"""Create the missing hash-bound human review receipt for a validated greenfield run."""
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
from governance import audit_logger
from governance.audit_logger import log_event
from governance.semantic_plan import digest

directory=Path(sys.argv[1]).resolve(); result=json.loads((directory/'result.json').read_text())
with sqlite3.connect(directory/'audit.db') as db:
    rows=[{'id':i,'event':e,'details':json.loads(d)} for i,e,d in db.execute('select id,event,details from audit_events order by id')]
bundle={'result':result,'audit':rows,'source_hashes':json.loads((directory/'source-hashes.json').read_text()),'maven_log_hash':digest((directory/'maven.log').read_text())}
bundle_hash=digest(bundle); audit_logger.DB_PATH=directory/'audit.db'
def review(state):
    decision=interrupt({'type':'GREENFIELD_RESULTS_REVIEW','bundle_hash':bundle_hash,'bundle':bundle,
        'message':'Approve verified greenfield results only; no release, merge or deployment authorization.'})
    if decision!='approve':
        log_event(result['workflow_id'],'greenfield_results_review','GREENFIELD_RESULTS_REJECTED',{'bundle_hash':bundle_hash});return {'status':'STOPPED_GREENFIELD_RESULTS_REJECTED'}
    log_event(result['workflow_id'],'greenfield_results_review','GREENFIELD_RESULTS_APPROVED',{'bundle_hash':bundle_hash,'decision_source':'explicit user decision'})
    return {'status':'GREENFIELD_RESULTS_REVIEWED','bundle_hash':bundle_hash}
g=StateGraph(dict);g.add_node('review',review);g.add_edge(START,'review');g.add_edge('review',END)
with SqliteSaver.from_conn_string(str(directory/'results-review.db')) as saver:
 app=g.compile(checkpointer=saver); config={'configurable':{'thread_id':result['workflow_id']+'-results-review'}}
 paused=app.invoke({},config)
 if not paused.get('__interrupt__'):raise RuntimeError('Expected review interrupt')
 final=app.invoke(Command(resume='approve'),config)
(directory/'results-review.json').write_text(json.dumps({**final,'bundle_hash':bundle_hash},indent=2)+'\n')
print(json.dumps(final,indent=2))
