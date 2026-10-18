"""Resume the exact retained checkpoint with consumed ordinals 1/2; never a new budget."""
import argparse, json, os, sqlite3, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.checkpoint.sqlite import SqliteSaver
from governance import audit_logger
from governance.audit_logger import log_event
from governance.brownfield_lifecycle import build
from governance.file_transaction import apply_transaction, ensure_workspace_ready
from governance.model_attempts import invoke_durable, request_lock
from governance.policy_engine import load_policy, enforce
from governance.semantic_plan import digest
from governance.test_safety import inventory, sha, assert_integrity
from llm.provider import get_model
from scripts.brownfield_live_demo import S,C,T

TEST = b'''package url_shortener;
import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;
import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;
@SpringBootTest @AutoConfigureMockMvc
class UrlStatusIntegrationTest {
 @Autowired MockMvc mvc;
 @Autowired UrlRepository repository;
 @BeforeEach void clean() { repository.deleteAll(); }
 @Test void statusPreservesClicksAndRedirectCompatibility() throws Exception {
  ShortUrl url = new ShortUrl("https://example.com/status", "status1");
  url.incrementClickCount(); repository.save(url);
  mvc.perform(get("/api/urls/status1/status")).andExpect(status().isOk())
   .andExpect(jsonPath("$.shortCode").value("status1"))
   .andExpect(jsonPath("$.expired").value(false));
  mvc.perform(get("/api/urls/status1/analytics")).andExpect(status().isOk())
   .andExpect(jsonPath("$.totalClicks").value(1));
  mvc.perform(get("/status1")).andExpect(status().is3xxRedirection())
   .andExpect(header().string("Location", "https://example.com/status"));
  mvc.perform(get("/api/urls/status1/analytics")).andExpect(status().isOk())
   .andExpect(jsonPath("$.totalClicks").value(2));
 }
 @Test void expirationStatesAreReadOnly() throws Exception {
  ShortUrl past = new ShortUrl("https://example.com/past", "past1");
  past.setExpiresAt(Instant.parse("2000-01-01T00:00:00Z")); repository.save(past);
  ShortUrl future = new ShortUrl("https://example.com/future", "future1");
  future.setExpiresAt(Instant.parse("2100-01-01T00:00:00Z")); repository.save(future);
  mvc.perform(get("/api/urls/past1/status")).andExpect(status().isOk()).andExpect(jsonPath("$.expired").value(true));
  mvc.perform(get("/api/urls/future1/status")).andExpect(status().isOk()).andExpect(jsonPath("$.expired").value(false));
  for (String code : new String[]{"past1","future1"})
   mvc.perform(get("/api/urls/"+code+"/analytics")).andExpect(status().isOk()).andExpect(jsonPath("$.totalClicks").value(0));
 }
 @Test void unknownIs404AndServiceDeclaresReadOnly() throws Exception {
  mvc.perform(get("/api/urls/missing/status")).andExpect(status().isNotFound());
  Transactional annotation = UrlService.class.getMethod("getStatus", String.class).getAnnotation(Transactional.class);
  assertNotNull(annotation); assertTrue(annotation.readOnly());
 }
}
'''

def verify(root):
 wid='e4e0d578-28db-40a3-944c-32a60be1722c'; workspace=root/'workspace'
 with sqlite3.connect('file:'+str(root/'audit.db')+'?mode=ro',uri=True) as c:
  events=[(e,json.loads(d)) for e,d in c.execute('select event,details from audit_events order by id')]
 receipt=[d for e,d in events if e=='EXECUTION_APPROVED'][-1]
 continuation=[d for e,d in events if e=='CONTINUATION_AUTHORIZED'][-1]
 plan={'task':'read-only URL status','production':[S,C],'test':T,'attempt_limit':3}
 baseline=inventory(workspace)
 if receipt['workflow_id']!=wid or receipt['plan_hash']!=digest(plan) or receipt['scope']!=[S,C,T]:raise PermissionError('Plan/scope mismatch')
 if receipt['baseline']!={p:sha(baseline[p]) for p in (S,C)} or receipt['policy_hash']!=digest(load_policy()):raise PermissionError('Baseline/policy mismatch')
 if continuation['parent_receipt_hash']!=digest(receipt) or continuation['parent_workflow_id']!=wid or continuation['workspace']!=str(workspace.resolve()) or continuation['allowed_attempts']!=[2,3]:raise PermissionError('Continuation receipt mismatch')
 with sqlite3.connect('file:'+str(root/'attempts.db')+'?mode=ro',uri=True) as c:
  attempts=c.execute('select ordinal,state from model_attempts where workflow_id=? order by ordinal',(wid,)).fetchall()
 if attempts!=[(1,'INTERRUPTED'),(2,'INTERRUPTED')]:raise PermissionError('Exactly two consumed interruptions required')
 if T in baseline:raise PermissionError('Test already exists')
 # Independent previously captured inventory protects every original project file.
 historical=json.loads((Path(__file__).resolve().parents[2]/'docs/evidence/assessment-scenarios/ollama-diagnosis/diagnostic.json').read_text())['before_hashes']
 expected={p.removeprefix('workspace/'):h for p,h in historical.items() if p.startswith('workspace/')}
 if {p:sha(b) for p,b in baseline.items()}!=expected:raise PermissionError('Protected inventory changed')
 ensure_workspace_ready(workspace)
 enforce({T:TEST},{T:None},workspace,wid,allow_new_test_files=[T])
 return wid,workspace,receipt,continuation,baseline

def main(root,cache,execute=False):
 audit_logger.DB_PATH=root/'audit.db'
 wid,workspace,receipt,continuation,baseline=verify(root)
 evidence=root/'attempt-3-evidence'
 if not execute:
  print(json.dumps({'verified':True,'workflow_id':wid,'remaining_attempt':3,'test_hash':sha(TEST)}));return
 evidence.mkdir(exist_ok=False)
 audit_logger.DB_PATH=root/'audit.db'
 def executor(state):
  if state['attempt']!=3:raise PermissionError('Only attempt 3 is authorized')
  verify(root); assert_integrity(workspace,baseline)
  sources={p:b.decode() for p,b in baseline.items() if p.startswith('url-shortener/src/main/java/')}
  context=json.loads((Path(__file__).resolve().parents[2]/'docs/evidence/assessment-scenarios/brownfield-proposal.json').read_text())
  prompt='''Return ONLY JSON {"files":[{"path":"exact approved path","content":"complete Java source"}]}. Final implementation attempt 3 of 3. Modify exactly UrlService.java and UrlController.java. Add GET /api/urls/{shortCode}/status delegating to public UrlService.getStatus(String shortCode). Return shortCode and expired. Use a read-only transaction, never increment clicks, preserve missing-code HTTP 404 and every existing method, constructor, and redirect/analytics behavior. Include necessary imports or fully qualified names. Do not modify tests/build/config. Historical diagnostic from a DIFFERENT failed execution: unresolved Map symbols due to missing java.util.Map import. This is context, not a failure of the current execution. No previous responses are available for consumed interrupted attempts 1/2. APPROVED REQUIREMENT/DESIGN:\n'''+json.dumps(context)+'\nAUTHORIZATION:\n'+json.dumps(receipt)+'\nCOMPLETE ORIGINAL JAVA SOURCES:\n'+json.dumps(sources)
  (evidence/'prompt.txt').write_text(prompt)
  log_event(wid,'brownfield_final','FINAL_ATTEMPT_DISPATCH_AUTHORIZED',{'ordinal':3,'receipt_hash':digest(receipt),'continuation_hash':digest(continuation),'prompt_hash':sha(prompt.encode())})
  response=invoke_durable(root/'attempts.db',wid,3,receipt,prompt,'qwen2.5-coder:latest',lambda:get_model().invoke(prompt,stage='implementation'),evidence/'response.txt')
  (evidence/'metadata.json').write_text(json.dumps(response.metadata,indent=2))
  raw=response.text.strip()
  if raw.startswith('```json') and raw.endswith('```'):raw=raw[7:-3].strip()
  payload=json.loads(raw)
  if set(payload)!={'files'} or not isinstance(payload['files'],list) or len(payload['files'])!=2:raise ValueError('Invalid complete-file schema')
  props={}
  for item in payload['files']:
   if set(item)!={'path','content'} or item['path'] not in (S,C) or item['path'] in props or not isinstance(item['content'],str):raise PermissionError('Invalid source scope')
   props[item['path']]=item['content'].encode()
  assert_integrity(workspace,baseline)
  tx=apply_transaction(props,{p:baseline[p] for p in props},workspace,[S,C],wid)
  return {'source_transaction':tx,'proposals':props,'proposal':payload,'proposal_hash':sha(response.text.encode())}
 def validator(state):
  expected={**state['proposals'],T:TEST};assert_integrity(workspace,baseline,expected)
  with (evidence/'maven.log').open('w') as log:
   process=subprocess.Popen(['./mvnw','-o',f'-Dmaven.repo.local={cache}','clean','test'],cwd=workspace/'url-shortener',stdout=log,stderr=subprocess.STDOUT)
   (evidence/'maven-process.json').write_text(json.dumps({'pid':process.pid}))
   code=process.wait()
  cases=[]
  for p in (workspace/'url-shortener/target/surefire-reports').glob('TEST-*.xml'):
   for case in ET.parse(p).getroot().iter('testcase'):
    cases.append({'class':case.get('classname'),'name':case.get('name'),'failed':case.find('failure') is not None,'error':case.find('error') is not None,'skipped':case.find('skipped') is not None})
  assert_integrity(workspace,baseline,expected)
  required={'statusPreservesClicksAndRedirectCompatibility','expirationStatesAreReadOnly','unknownIs404AndServiceDeclaresReadOnly'}
  passed=code==0 and len(cases)==11 and required.issubset({c['name'] for c in cases}) and not any(c['failed'] or c['error'] or c['skipped'] for c in cases)
  result={'passed':passed,'exit_code':code,'cases':cases,'diagnostics':(evidence/'maven.log').read_text()}
  (evidence/'maven-result.json').write_text(json.dumps(result,indent=2));return result
 with SqliteSaver.from_conn_string(str(root/'workflow.db')) as saver:
  app=build(executor,validator).compile(checkpointer=saver);cfg={'configurable':{'thread_id':wid}}
  snap=app.get_state(cfg)
  if snap.next!=('implement',) or not snap.values.get('approved') or snap.values['plan_hash']!=receipt['plan_hash']:raise PermissionError('Checkpoint cannot safely continue')
  # Same graph/thread, checkpoint reconciliation only; no new approval or budget.
  log_event(wid,'brownfield_final','CHECKPOINT_ATTEMPT_ACCOUNTING_RECONCILED',{'from':snap.values.get('attempt'),'to':2,'consumed':[1,2],'continuation_hash':digest(continuation),'decision_source':'explicit user instruction to use final attempt'})
  app.update_state(cfg,{**snap.values,'attempt':2,'test_bytes':TEST,'execution_receipt':receipt},as_node='approval')
  result=app.invoke(None,cfg);snap=app.get_state(cfg)
  exported={'status':result.get('status'),'attempt':result.get('attempt'),'next':list(snap.next),'interrupts':[i.value for t in snap.tasks for i in t.interrupts],'workflow_id':wid}
  (evidence/'result.json').write_text(json.dumps(exported,indent=2));print(json.dumps(exported),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--maven-cache',type=Path,required=True);p.add_argument('--execute',action='store_true');a=p.parse_args();main(a.directory.resolve(),a.maven_cache.resolve(),a.execute)
