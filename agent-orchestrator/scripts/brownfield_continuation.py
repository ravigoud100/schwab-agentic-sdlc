"""One approved continuation: imports consumed attempt history; permits only 2/3."""
import json,sys,uuid,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from governance import audit_logger
from governance.audit_logger import log_event
from governance.model_attempts import reconcile,invoke_durable
from governance.policy_engine import load_policy
from governance.test_safety import inventory,sha
from governance.semantic_plan import digest
from llm.provider import get_model
root=Path(sys.argv[1]);wid='e4e0d578-28db-40a3-944c-32a60be1722c';audit_logger.DB_PATH=root/'audit.db'
receipt=json.loads((root/'receipt.json').read_text()) if (root/'receipt.json').exists() else None
if receipt is None:
 with sqlite3.connect(root/'audit.db') as db:
  row=db.execute("select details from audit_events where event='EXECUTION_APPROVED' order by id desc limit 1").fetchone()
 receipt=json.loads(row[0]) if row else None
if not receipt:raise PermissionError('Original receipt missing')
state=reconcile(root/'attempts.db',wid)
if state['consumed_ordinals']!=[1] or state['next_ordinal']!=2:raise PermissionError('Continuation requires exactly consumed interrupted attempt 1')
workspace=root/'workspace';baseline=inventory(workspace);paths=['url-shortener/src/main/java/url_shortener/UrlService.java','url-shortener/src/main/java/url_shortener/UrlController.java','url-shortener/src/test/java/url_shortener/UrlStatusIntegrationTest.java']
if {p:sha(baseline[p]) for p in paths[:2]}!=receipt['baseline'] or digest(load_policy())!=receipt['policy_hash'] or receipt['scope']!=paths:raise PermissionError('Receipt/baseline/policy/scope changed')
continuation={'continuation_id':str(uuid.uuid4()),'parent_workflow_id':wid,'parent_receipt_hash':digest(receipt),'consumed_attempts':[1],'allowed_attempts':[2,3],'workspace':str(workspace.resolve())}
log_event(wid,'brownfield_continuation','CONTINUATION_AUTHORIZED',{**continuation,'decision_source':'explicit user decision'})
sources={p:(workspace/p).read_text() for p in paths[:2]};prompt='''Return ONLY JSON {"files":[{"path":"exact approved path","content":"complete Java source"}]}. This is bounded continuation attempt 2 of 3. Modify exactly UrlService.java and UrlController.java. Add a read-only GET /api/urls/{shortCode}/status. Return java.util.Map<String,Object> using fully qualified java.util.Map.of; do not rely on imports. Preserve 404 via ResponseStatusException and never increment clicks. Historical unrelated attempt 1 compiler diagnostic (do not claim it occurred now): cannot find symbol Map. No tests/config/prose. ORIGINALS:\n'''+json.dumps(sources)
(root/'continuation-2-prompt.txt').write_text(prompt)
try:
 response=invoke_durable(root/'attempts.db',wid,2,receipt,prompt,'qwen2.5-coder:latest',lambda:get_model().invoke(prompt,stage='implementation'),root/'continuation-2-response.txt')
 log_event(wid,'brownfield_continuation','CONTINUATION_MODEL_RESPONSE',{'attempt':2,'response_hash':sha(response.text.encode())})
except BaseException as error:
 log_event(wid,'brownfield_continuation','CONTINUATION_MODEL_FAILED',{'attempt':2,'error_type':type(error).__name__});raise
