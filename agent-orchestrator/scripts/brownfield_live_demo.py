"""Fresh approved brownfield status run; stops after Maven at results review."""
import argparse,json,shutil,subprocess,sys,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from langgraph.graph import StateGraph,START,END
from langgraph.types import interrupt,Command
from langgraph.checkpoint.sqlite import SqliteSaver
from governance import audit_logger
from governance.audit_logger import log_event
from governance.file_transaction import apply_transaction,finish_transaction
from governance.policy_engine import enforce
from governance.test_safety import inventory,new_test_target,sha
from llm.provider import get_model
S='url-shortener/src/main/java/url_shortener/UrlService.java';C='url-shortener/src/main/java/url_shortener/UrlController.java';T='url-shortener/src/test/java/url_shortener/UrlStatusIntegrationTest.java'
TEST=b'''package url_shortener;\nimport static org.springframework.http.MediaType.APPLICATION_JSON;\nimport static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;\nimport static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;\nimport static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;\nimport static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;\nimport org.junit.jupiter.api.BeforeEach; import org.junit.jupiter.api.Test; import org.springframework.beans.factory.annotation.Autowired; import org.springframework.boot.test.context.SpringBootTest; import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc; import org.springframework.test.web.servlet.MockMvc; import org.springframework.test.web.servlet.MvcResult;\n@SpringBootTest @AutoConfigureMockMvc class UrlStatusIntegrationTest { @Autowired MockMvc mvc; @Autowired UrlRepository repository; @BeforeEach void clean(){repository.deleteAll();} @Test void statusIsReadOnlyAndUnknownIs404() throws Exception { MvcResult created=mvc.perform(post("/api/urls").contentType(APPLICATION_JSON).content("{\\"originalUrl\\":\\"https://example.com/status\\"}" )).andExpect(status().isCreated()).andReturn(); String body=created.getResponse().getContentAsString(); String code=body.replaceAll(".*\\\\\"shortCode\\\\\":\\\\\"([^\\\\\"]+)\\\\\".*","$1"); mvc.perform(get("/api/urls/"+code+"/status")).andExpect(status().isOk()).andExpect(jsonPath("$.shortCode").value(code)).andExpect(jsonPath("$.expired").value(false)); mvc.perform(get("/api/urls/"+code+"/analytics")).andExpect(status().isOk()).andExpect(jsonPath("$.totalClicks").value(0)); mvc.perform(get("/api/urls/missing/status")).andExpect(status().isNotFound()); }}\n'''
def main(d,cache):
 d.mkdir(parents=True,exist_ok=False);root=d/'workspace';root.mkdir();shutil.copytree(Path(__file__).resolve().parents[2]/'url-shortener',root/'url-shortener',ignore=shutil.ignore_patterns('target','.git','.jdk-25'));before=inventory(root);wid=str(uuid.uuid4());audit_logger.DB_PATH=d/'audit.db';ph='37159ec6198899542756efd74b502926a2d27d89a744875634a2ebaed7947848'
 def review(st):
  x=interrupt({'type':'BROWNFIELD_PLAN_APPROVAL','proposal_hash':ph,'scope':[S,C,T]});
  if x!='approve':return {'approved':False}
  log_event(wid,'brownfield_demo','BROWNFIELD_PLAN_APPROVED',{'proposal_hash':ph,'decision_source':'explicit user decision'});return {'approved':True}
 g=StateGraph(dict);g.add_node('review',review);g.add_edge(START,'review');g.add_edge('review',END)
 with SqliteSaver.from_conn_string(str(d/'workflow.db')) as saver:
  a=g.compile(checkpointer=saver);cfg={'configurable':{'thread_id':wid}};p=a.invoke({},cfg);state=a.invoke(Command(resume='approve'),cfg)
 if not state['approved']:raise PermissionError('unapproved')
 src={p:(root/p).read_text() for p in (S,C)};prompt='''Return ONLY JSON {"files":[{"path":"...","content":"complete Java source"}]}. Modify exactly UrlService.java and UrlController.java. Add a read-only GET /api/urls/{shortCode}/status endpoint. Service must use @org.springframework.transaction.annotation.Transactional(readOnly = true), preserve 404 via ResponseStatusException, return java.util.Map with shortCode and expired using Instant.now; it must not increment clicks. Controller delegates only. Preserve every existing method and behavior. No tests/config/prose. ORIGINALS:\n'''+json.dumps(src)
 r=get_model().invoke(prompt,stage='implementation');(d/'prompt.txt').write_text(prompt);(d/'response.txt').write_text(r.text);raw=r.text.strip();raw=raw[7:-3].strip() if raw.startswith('```json') and raw.endswith('```') else raw;files=json.loads(raw)['files'];props={x['path']:(x['content'].strip()+'\n').encode() for x in files}
 if set(props)!={S,C}:raise ValueError('model scope')
 tx=apply_transaction(props,{p:src[p].encode() for p in src},root,[S,C],wid)
 try:
  enforce({T:TEST},{T:None},root,wid,allow_new_test_files=[T]);new_test_target(root,T).write_bytes(TEST);log_event(wid,'brownfield_demo','TEST_FILE_CREATED',{'path':T,'sha256':sha(TEST)})
  q=subprocess.run(['./mvnw','-o',f'-Dmaven.repo.local={cache}','clean','test'],cwd=root/'url-shortener',text=True,capture_output=True,timeout=180);(d/'maven.log').write_text(q.stdout+'\n'+q.stderr)
  if q.returncode:raise RuntimeError('maven failed')
  finish_transaction(tx['transaction_id'],root,[S,C],passed=True);out={'status':'AWAITING_BROWNFIELD_RESULTS_REVIEW','workflow_id':wid,'transaction_id':tx['transaction_id'],'maven_exit_code':0,'changed_files':sorted(p for p,v in inventory(root).items() if before.get(p)!=v),'model':'real local Ollama','test':'trusted renderer'};log_event(wid,'brownfield_demo','WORKFLOW_FINISHED',out);(d/'result.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
 except BaseException:
  finish_transaction(tx['transaction_id'],root,[S,C],passed=False);raise
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--maven-cache',type=Path,required=True);a=p.parse_args();main(a.directory.resolve(),a.maven_cache.resolve())
