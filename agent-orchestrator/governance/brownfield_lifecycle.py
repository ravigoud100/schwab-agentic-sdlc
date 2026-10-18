"""Approval-gated brownfield lifecycle with checkpointed continuation.

Callbacks are intentionally injected: fixtures can prove ordering/recovery without
claiming a model or Maven run. A live adapter must supply the same callbacks.
"""
from pathlib import Path
from langgraph.graph import StateGraph,START,END
from langgraph.types import interrupt
from governance.audit_logger import log_event
from governance.file_transaction import finish_transaction
from governance.additive_transaction import create_test,rollback_test,finish_test
from governance.rollback_manager import file_hash

MAX_ATTEMPTS=3

def build(executor, validator):
 def approval(state):
  d=interrupt({'type':'BROWNFIELD_PLAN_APPROVAL','plan_hash':state['plan_hash'],'scope':state['scope']})
  if d!='approve':return {**state,'status':'STOPPED_PLAN_REJECTED'}
  log_event(state['workflow_id'],'brownfield_lifecycle','BROWNFIELD_PLAN_AUTHORIZED',{'plan_hash':state['plan_hash'],'scope':state['scope']})
  return {**state,'approved':True,'attempt':0}
 def implement(state):
  if not state.get('approved'):raise PermissionError('Fresh plan approval required')
  if state['attempt']>=MAX_ATTEMPTS:return {**state,'status':'STOPPED_RETRY_EXHAUSTED'}
  attempt=state['attempt']+1
  try:result=executor({**state,'attempt':attempt})
  except Exception as error:
   log_event(state['workflow_id'],'brownfield_lifecycle','BROWNFIELD_IMPLEMENTATION_STOPPED',{'attempt':attempt,'error':str(error)})
   return {**state,'attempt':attempt,'status':'STOPPED_IMPLEMENTATION_FAILED','error':str(error)}
  log_event(state['workflow_id'],'brownfield_lifecycle','BROWNFIELD_IMPLEMENTATION_ATTEMPT',{'attempt':attempt,'proposal':result.get('proposal_hash')})
  return {**state,'attempt':attempt,**result}
 def test(state):
  try:record=create_test(state['test_path'],state['test_bytes'],state['workspace'],state['workflow_id'],[state['test_path']])
  except Exception as error:
   # If publication may have occurred, retain its journal for recovery rather
   # than deleting an unknown file or claiming whole-set compensation.
   if (Path(state['workspace'])/state['test_path']).exists():
    return {**state,'status':'STOPPED_RECOVERY_BLOCKED','error':str(error)}
   finish_transaction(state['source_transaction']['transaction_id'],Path(state['workspace']),state['production_paths'],passed=False)
   return {**state,'status':'STOPPED_TEST_CREATION_FAILED','error':str(error)}
  log_event(state['workflow_id'],'brownfield_lifecycle','BROWNFIELD_TEST_CREATED',{'transaction_id':record['transaction_id'],'path':state['test_path'],'hash':record['created_hash']})
  return {**state,'test_transaction':record}
 def validate(state):
  try:result=validator(state)
  except Exception as error:result={'passed':False,'diagnostics':str(error),'infrastructure_error':True}
  log_event(state['workflow_id'],'brownfield_lifecycle','BROWNFIELD_MAVEN_VALIDATED',{'attempt':state['attempt'],'passed':result.get('passed'),'diagnostics':result.get('diagnostics','')})
  if result.get('passed'):
   finish_transaction(state['source_transaction']['transaction_id'],Path(state['workspace']),state['production_paths'],passed=True)
   finish_test(state['test_transaction'],state['workspace'])
   return {**state,'status':'AWAITING_BROWNFIELD_RESULTS_REVIEW','validation':result}
  try:
   # Preflight production bytes before deleting the additive test.
   for path,expected in state['source_transaction']['expected_hashes'].items():
    target=Path(state['workspace'])/path
    if target.is_symlink() or not target.is_file() or file_hash(target.read_bytes())!=expected:
     raise PermissionError('Production file changed externally; compensation blocked')
   rollback_test(state['test_transaction'],Path(state['workspace']))
   finish_transaction(state['source_transaction']['transaction_id'],Path(state['workspace']),state['production_paths'],passed=False)
  except Exception as error:return {**state,'status':'STOPPED_RECOVERY_BLOCKED','recovery_error':str(error),'validation':result}
  from governance.java_import_diagnostics import diagnose_sources_imports
  import_diag = ""
  if state.get('proposals'):
   props_str = {p: b.decode('utf-8', errors='replace') for p, b in state['proposals'].items() if p.endswith('.java')}
   id_res = diagnose_sources_imports(props_str)
   if not id_res.get('valid'):
    import_diag = id_res.get('summary', '')
  return {**state,'status':'RECOVERY_REQUIRED' if state['attempt']<MAX_ATTEMPTS else 'STOPPED_RETRY_EXHAUSTED','validation':result,
          'failed_proposal':state.get('proposal'),'failed_sources':state.get('proposals'),
          'compiler_diagnostics':result.get('diagnostics',''),
          'import_diagnostics':import_diag}
 def review(state):
  d=interrupt({'type':'BROWNFIELD_RESULTS_REVIEW','validation':state['validation'],'attempt':state['attempt']})
  return {**state,'status':'BROWNFIELD_RESULTS_REVIEWED' if d=='approve' else 'STOPPED_RESULTS_REJECTED'}
 g=StateGraph(dict);g.add_node('approval',approval);g.add_node('implement',implement);g.add_node('test',test);g.add_node('validate',validate);g.add_node('review',review)
 g.add_edge(START,'approval');g.add_conditional_edges('approval',lambda s:'go' if s.get('approved') else 'stop',{'go':'implement','stop':END})
 g.add_conditional_edges('implement',lambda s:'stop' if s.get('status','').startswith('STOPPED') else 'test',{'stop':END,'test':'test'});g.add_conditional_edges('test',lambda s:'stop' if s.get('status','').startswith('STOPPED') else 'validate',{'stop':END,'validate':'validate'});g.add_conditional_edges('validate',lambda s:'retry' if s['status']=='RECOVERY_REQUIRED' and s['attempt']<MAX_ATTEMPTS else 'review' if s['status']=='AWAITING_BROWNFIELD_RESULTS_REVIEW' else 'stop',{'retry':'implement','review':'review','stop':END});g.add_edge('review',END);return g
