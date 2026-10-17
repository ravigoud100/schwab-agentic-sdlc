import json
from pathlib import Path
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.brownfield_lifecycle import build
from governance.file_transaction import apply_transaction

S='url-shortener/src/main/java/url_shortener/UrlService.java';C='url-shortener/src/main/java/url_shortener/UrlController.java';T='url-shortener/src/test/java/url_shortener/Generated.java'
def setup(tmp_path):
 root=tmp_path/'work';
 for p in (S,C):
  target=root/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('class '+Path(p).stem+' {}')
 (root/T).parent.mkdir(parents=True,exist_ok=True)
 return root
def run(tmp_path,fail=False,external=False):
 root=setup(tmp_path);audit_logger.DB_PATH=tmp_path/'audit.db';calls=[]
 def executor(state):
  originals={p:(root/p).read_bytes() for p in (S,C)};props={p:originals[p]+b'\n// changed' for p in (S,C)}
  tx=apply_transaction(props,originals,root,[S,C],state['workflow_id']);return {'source_transaction':tx,'proposals':props,'proposal':{'attempt':state['attempt']},'proposal_hash':'p'}
 def validator(state):
  calls.append(state['attempt'])
  if external:(root/T).write_text('external')
  return {'passed':not fail or state['attempt']==2,'diagnostics':'cannot find symbol Map' if fail else ''}
 state={'workflow_id':'b','workspace':str(root),'plan_hash':'fresh','scope':[S,C,T],'production_paths':[S,C],'test_path':T,'test_bytes':b'class Generated {}'}
 with SqliteSaver.from_conn_string(str(tmp_path/'flow.db')) as saver:
  app=build(executor,validator).compile(checkpointer=saver);cfg={'configurable':{'thread_id':'b'}};paused=app.invoke(state,cfg);assert paused['__interrupt__'][0].value['type']=='BROWNFIELD_PLAN_APPROVAL';out=app.invoke(Command(resume='approve'),cfg)
  if out.get('status')=='AWAITING_BROWNFIELD_RESULTS_REVIEW':assert out['__interrupt__'][0].value['type']=='BROWNFIELD_RESULTS_REVIEW'
 return root,out,calls
def test_checkpoint_continues_after_approval_and_reaches_results_review(tmp_path):
 root,out,calls=run(tmp_path);assert out['status']=='AWAITING_BROWNFIELD_RESULTS_REVIEW';assert calls==[1];assert (root/T).exists()
def test_compilation_recovery_rolls_back_whole_workspace_then_retries(tmp_path):
 root,out,calls=run(tmp_path,fail=True);assert out['status']=='AWAITING_BROWNFIELD_RESULTS_REVIEW';assert calls==[1,2];assert (root/S).read_text().endswith('// changed')
def test_external_generated_test_blocks_destructive_compensation(tmp_path):
 root,out,calls=run(tmp_path,fail=True,external=True);assert out['status']=='STOPPED_RECOVERY_BLOCKED';assert (root/T).read_text()=='external'
