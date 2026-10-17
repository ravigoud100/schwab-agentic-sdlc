"""One approved, isolated greenfield run with an actual LangGraph review gate.

The component source is requested from Ollama.  The small unit test is a vetted
renderer because the reviewed test scope allows only that exact file.
"""
import argparse, json, shutil, sqlite3, subprocess, sys, tempfile, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
from governance import audit_logger
from governance.audit_logger import log_event
from governance.assessment_scenarios import GREENFIELD, apply_approved_greenfield, finish_greenfield
from governance.policy_engine import enforce
from governance.test_safety import inventory, new_test_target, sha
from llm.provider import get_model

TEST = 'url-shortener/src/test/java/url_shortener/UrlHealthComponentTest.java'
TEST_SOURCE = b'''package url_shortener;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class UrlHealthComponentTest {
    @Test
    void statusIsReady() {
        assertEquals("ready", new UrlHealthComponent().status());
    }
}
'''

def gate(workflow_id, proposal_hash):
    def review(state):
        decision = interrupt({'type':'GREENFIELD_PLAN_APPROVAL','proposal_hash':proposal_hash,
            'scope':[GREENFIELD, TEST], 'message':'Approve only this isolated component and additive test scope; no release authorization.'})
        if decision != 'approve':
            log_event(workflow_id,'greenfield_demo','GREENFIELD_PLAN_REJECTED',{'proposal_hash':proposal_hash})
            return {**state,'approved':False}
        log_event(workflow_id,'greenfield_demo','GREENFIELD_PLAN_APPROVED',{'proposal_hash':proposal_hash,'decision_source':'explicit user decision'})
        return {**state,'approved':True}
    graph=StateGraph(dict); graph.add_node('review',review); graph.add_edge(START,'review'); graph.add_edge('review',END)
    return graph

def dump(path,obj): path.write_text(json.dumps(obj,indent=2,default=str)+'\n')

def main(directory, cache):
    directory.mkdir(parents=True,exist_ok=False); workflow_id=str(uuid.uuid4()); root=directory/'workspace'; root.mkdir()
    source_root=Path(__file__).resolve().parents[2]/'url-shortener'
    shutil.copytree(source_root,root/'url-shortener',ignore=shutil.ignore_patterns('target','.git','.jdk-25'))
    before=inventory(root); audit_logger.DB_PATH=directory/'audit.db'; proposal_hash='a0c5a874b05bb820e65ccef8a45124d34f51181a4233e4d71e3292ad1000aae8'
    log_event(workflow_id,'greenfield_demo','WORKFLOW_STARTED',{'scenario':'greenfield','workspace':str(root),'proposal_hash':proposal_hash})
    config={'configurable':{'thread_id':workflow_id}}
    with SqliteSaver.from_conn_string(str(directory/'workflow.db')) as saver:
        app=gate(workflow_id,proposal_hash).compile(checkpointer=saver)
        paused=app.invoke({},config)
        if not paused.get('__interrupt__'): raise RuntimeError('Expected human approval interrupt')
        # This resume is the user's explicit approval in this task, recorded above.
        state=app.invoke(Command(resume='approve'),config)
    if not state['approved']: raise PermissionError('Plan not approved')
    prompt='''Return ONLY JSON {"files":[{"path":"url-shortener/src/main/java/url_shortener/UrlHealthComponent.java","content":"complete Java source"}]}. Create a Java 21 Spring @Component in package url_shortener named UrlHealthComponent. It has public String status() returning exactly "ready". No constructors, dependencies, I/O, logging, commands, configuration, other files, Markdown, or prose.'''
    response=get_model().invoke(prompt,stage='implementation'); (directory/'component-prompt.txt').write_text(prompt); (directory/'component-response.txt').write_text(response.text)
    raw=response.text.strip()
    if raw.startswith('```json') and raw.endswith('```'):
        raw=raw[len('```json'): -len('```')].strip()
    payload=json.loads(raw); files=payload.get('files') if isinstance(payload,dict) else None
    if not isinstance(files,list) or len(files)!=1 or files[0].get('path')!=GREENFIELD or not isinstance(files[0].get('content'),str): raise ValueError('Invalid live greenfield proposal')
    content=(files[0]['content'].strip()+'\n').encode()
    if not all(token in content.decode() for token in ('package url_shortener;', 'class UrlHealthComponent', 'String status()', '"ready"')): raise ValueError('Live component misses reviewed contract')
    tx=apply_approved_greenfield(root,workflow_id,content,approved=True)
    try:
        enforce({TEST:TEST_SOURCE},{TEST:None},root,workflow_id,allow_new_test_files=[TEST])
        target=new_test_target(root,TEST); target.write_bytes(TEST_SOURCE)
        log_event(workflow_id,'greenfield_demo','TEST_SCOPE_APPROVED',{'path':TEST,'renderer':'vetted deterministic unit-test renderer'})
        log_event(workflow_id,'greenfield_demo','TEST_FILE_CREATED',{'path':TEST,'sha256':sha(TEST_SOURCE)})
        command=['./mvnw','-o',f'-Dmaven.repo.local={cache}','clean','test']
        run=subprocess.run(command,cwd=root/'url-shortener',text=True,capture_output=True,timeout=180)
        (directory/'maven.log').write_text(run.stdout+'\n'+run.stderr)
        if run.returncode: raise RuntimeError('Maven validation failed')
        finish_greenfield(tx,root,passed=True)
        current=inventory(root); changed=sorted(p for p in current if before.get(p)!=current[p])
        result={'status':'AWAITING_GREENFIELD_RESULTS_REVIEW','workflow_id':workflow_id,'transaction_id':tx['transaction_id'],'changed_files':changed,'maven_exit_code':run.returncode,'component_model':'real local Ollama','test_provenance':'vetted deterministic renderer','release_authorized':False}
    except BaseException as error:
        finish_greenfield(tx,root,passed=False); raise
    log_event(workflow_id,'greenfield_demo','WORKFLOW_FINISHED',result); dump(directory/'result.json',result)
    with sqlite3.connect(directory/'audit.db') as conn: dump(directory/'audit.json',[{'id':i,'event':e,'details':json.loads(d)} for i,e,d in conn.execute('select id,event,details from audit_events order by id')])
    dump(directory/'source-hashes.json',{p:sha(data) for p,data in inventory(root).items() if p in changed}); print(json.dumps(result,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--maven-cache',type=Path,required=True);a=p.parse_args();main(a.directory.resolve(),a.maven_cache.resolve())
