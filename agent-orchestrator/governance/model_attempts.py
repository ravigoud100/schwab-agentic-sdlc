"""Durable attempt accounting written before an external model request."""
import hashlib,json,sqlite3,uuid,fcntl,os
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime,timezone

def _hash(value):return hashlib.sha256(value.encode()).hexdigest()
def _now():return datetime.now(timezone.utc).isoformat()
def _db(path):
 conn=sqlite3.connect(Path(path));conn.execute('''create table if not exists model_attempts(
 attempt_id text primary key, workflow_id text not null, ordinal integer not null,
 receipt_hash text not null, prompt_hash text not null, model text not null,
 state text not null, created_at text not null, updated_at text not null, response_hash text, error text)''');return conn
def start(path,workflow_id,ordinal,receipt,prompt,model):
 if ordinal<1:raise ValueError('attempt ordinal must be positive')
 with _db(path) as c:
  c.execute('BEGIN IMMEDIATE')
  existing=c.execute('select state from model_attempts where workflow_id=? and ordinal=?',(workflow_id,ordinal)).fetchone()
  if existing:raise PermissionError('Attempt ordinal already reserved')
  ident=str(uuid.uuid4());receipt_hash=_hash(json.dumps(receipt,sort_keys=True));now=_now()
  c.execute('insert into model_attempts values(?,?,?,?,?,?,?,?,?,?,?)',(ident,workflow_id,ordinal,receipt_hash,_hash(prompt),model,'STARTED',now,now,None,None))
 return ident
def finish(path,attempt_id,response):
 with _db(path) as c:
  row=c.execute('select state from model_attempts where attempt_id=?',(attempt_id,)).fetchone()
  if not row or row[0]!='STARTED':raise PermissionError('Only a started attempt may complete')
  c.execute('update model_attempts set state=?,response_hash=?,updated_at=? where attempt_id=?',('COMPLETED',_hash(response),_now(),attempt_id))
def fail(path,attempt_id,error):
 with _db(path) as c:
  row=c.execute('select state from model_attempts where attempt_id=?',(attempt_id,)).fetchone()
  if not row or row[0]!='STARTED':raise PermissionError('Only a started attempt may fail')
  c.execute('update model_attempts set state=?,error=?,updated_at=? where attempt_id=?',('FAILED',type(error).__name__,_now(),attempt_id))
def reconcile(path,workflow_id):
 """Unfinished dispatched requests count conservatively; never retry them silently."""
 with request_lock(path):
  return _reconcile_unowned(path,workflow_id)

def _reconcile_unowned(path,workflow_id):
 with _db(path) as c:
  rows=c.execute('select attempt_id,ordinal,state from model_attempts where workflow_id=? order by ordinal',(workflow_id,)).fetchall()
  uncertain=[{'attempt_id':i,'ordinal':o,'state':s} for i,o,s in rows if s=='STARTED']
  for item in uncertain:c.execute('update model_attempts set state=?,updated_at=? where attempt_id=?',('INTERRUPTED',_now(),item['attempt_id']))
 return {'consumed_ordinals':[o for _,o,_ in rows],'interrupted':uncertain,'next_ordinal':(max([o for _,o,_ in rows],default=0)+1)}

@contextmanager
def request_lock(path):
 """Kernel ownership lasts through response persistence; released on process exit."""
 with open(str(path)+'.request.lock','a+b') as stream:
  try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise PermissionError('Model request owner is still active')
  try:yield
  finally:fcntl.flock(stream,fcntl.LOCK_UN)

def persist_response(path,response):
 """No-overwrite response capture, durable before the ledger completion commit."""
 path=Path(path)
 with path.open('xb') as stream:
  stream.write(response.encode());stream.flush();os.fsync(stream.fileno())
 fd=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)

def invoke_durable(path,workflow_id,ordinal,receipt,prompt,model,invoke,response_path):
 with request_lock(path):
  with _db(path) as c:
   rows=c.execute('select ordinal,state from model_attempts where workflow_id=? order by ordinal',(workflow_id,)).fetchall()
  if ordinal>3 or [r[0] for r in rows]!=list(range(1,ordinal)) or any(r[1]=='STARTED' for r in rows):
   raise PermissionError('Unreconciled history or exhausted attempt budget')
  ident=start(path,workflow_id,ordinal,receipt,prompt,model)
  try:
   response=invoke()
   persist_response(response_path,response.text)
   finish(path,ident,response.text)
   return response
  except BaseException as error:
   # Do not mask an original error or reopen an externally interrupted attempt.
   with _db(path) as c:
    state=c.execute('select state from model_attempts where attempt_id=?',(ident,)).fetchone()
   if state and state[0]=='STARTED':fail(path,ident,error)
   raise
