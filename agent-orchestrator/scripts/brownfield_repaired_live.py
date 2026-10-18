"""Fresh approval-bound brownfield execution using the repaired lifecycle."""
import argparse,json,shutil,subprocess,sys,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.audit_logger import log_event
from governance.brownfield_lifecycle import build
from governance.file_transaction import apply_transaction
from governance.policy_engine import load_policy
from governance.semantic_plan import digest
from governance.test_safety import inventory,sha
from governance.model_attempts import invoke_durable,reconcile
from llm.provider import get_model
from scripts.brownfield_live_demo import S,C,T,TEST
def main(d,cache):
 d.mkdir(parents=True,exist_ok=False);root=d/'workspace';root.mkdir();shutil.copytree(Path(__file__).resolve().parents[2]/'url-shortener',root/'url-shortener',ignore=shutil.ignore_patterns('target','.git','.jdk-25'))
 wid=str(uuid.uuid4());audit_logger.DB_PATH=d/'audit.db';baseline=inventory(root);scope=[S,C,T];plan={'task':'read-only URL status','production':[S,C],'test':T,'attempt_limit':3};receipt={'workflow_id':wid,'plan_hash':digest(plan),'scope':scope,'baseline':{p:sha(baseline[p]) for p in (S,C)},'policy_hash':digest(load_policy())}
 log_event(wid,'brownfield_repaired','EXECUTION_APPROVAL_REQUESTED',receipt)
 def executor(state):
  originals={p:(root/p).read_bytes() for p in (S,C)};sources={p:b.decode() for p,b in originals.items()};previous=state.get('compiler_diagnostics','')
  prompt='''Return ONLY JSON {"files":[{"path":"exact approved path","content":"complete Java source"}]}. Modify exactly UrlService.java and UrlController.java. Add read-only GET /api/urls/{shortCode}/status. The service must return java.util.Map<String,Object> containing shortCode and expired, be @org.springframework.transaction.annotation.Transactional(readOnly = true), preserve ResponseStatusException 404, and never increment clicks. Controller delegates. Preserve all existing methods. Use java.util.Map fully qualified so no import is required. No config/tests/prose. ORIGINALS:\n'''+json.dumps(sources)+'\nPREVIOUS GENUINE COMPILER DIAGNOSTICS:\n'+previous
  (d/f'attempt-{state["attempt"]}-prompt.txt').write_text(prompt)
  response=invoke_durable(d/'attempts.db',wid,state['attempt'],receipt,prompt,'qwen2.5-coder:latest',lambda:get_model().invoke(prompt,stage='implementation'),d/f'attempt-{state["attempt"]}-response.txt')
  raw=response.text.strip();raw=raw[7:-3].strip() if raw.startswith('```json') and raw.endswith('```') else raw;files=json.loads(raw)['files'];props={x['path']:(x['content'].strip()+'\n').encode() for x in files}
  if set(props)!={S,C}:raise PermissionError('Model expanded or reduced approved scope')
  tx=apply_transaction(props,originals,root,[S,C],wid)
  return {'source_transaction':tx,'proposals':props,'proposal':{'raw_hash':sha(response.text.encode()),'paths':sorted(props)},'proposal_hash':sha(response.text.encode())}
 def validator(state):
  q=subprocess.run(['./mvnw','-o',f'-Dmaven.repo.local={cache}','clean','test'],cwd=root/'url-shortener',capture_output=True,text=True,timeout=180);out=q.stdout+'\n'+q.stderr;(d/f'attempt-{state["attempt"]}-maven.log').write_text(out)
  return {'passed':q.returncode==0,'exit_code':q.returncode,'diagnostics':out[-8000:]}
 accounting=reconcile(d/'attempts.db',wid)
 if accounting['interrupted']:raise PermissionError('Interrupted model attempt requires new authorization; no dispatch')
 state={'workflow_id':wid,'workspace':str(root),'plan_hash':receipt['plan_hash'],'scope':scope,'production_paths':[S,C],'test_path':T,'test_bytes':TEST,'execution_receipt':receipt}
 with SqliteSaver.from_conn_string(str(d/'workflow.db')) as saver:
  app=build(executor,validator).compile(checkpointer=saver);cfg={'configurable':{'thread_id':wid}};paused=app.invoke(state,cfg)
  if not paused.get('__interrupt__'):raise RuntimeError('Missing plan approval interrupt')
  log_event(wid,'brownfield_repaired','EXECUTION_APPROVED',{**receipt,'decision_source':'explicit user decision'})
  result=app.invoke(Command(resume='approve'),cfg);snap=app.get_state(cfg)
 (d/'receipt.json').write_text(json.dumps(receipt,indent=2));(d/'result.json').write_text(json.dumps({'status':result.get('status'),'interrupts':[i.value for t in snap.tasks for i in t.interrupts],'attempt':result.get('attempt')},indent=2))
 print((d/'result.json').read_text())
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--maven-cache',type=Path,required=True);a=p.parse_args();main(a.directory.resolve(),a.maven_cache.resolve())
