import sqlite3
from types import SimpleNamespace
import pytest
from governance import model_attempts as m

def call(tmp_path, invoke, ordinal=1):
    return m.invoke_durable(tmp_path/'a.db','w',ordinal,{},'prompt','model',invoke,tmp_path/'response.txt')

def test_active_request_cannot_be_reconciled(tmp_path):
    def invoke():
        with pytest.raises(PermissionError, match='active'):m.reconcile(tmp_path/'a.db','w')
        return SimpleNamespace(text='raw response')
    call(tmp_path,invoke)
    assert (tmp_path/'response.txt').read_text()=='raw response'
    assert m.reconcile(tmp_path/'a.db','w')['interrupted']==[]

def test_response_is_durable_before_completion(tmp_path,monkeypatch):
    original=m.finish
    def finish(path,ident,response):
        assert (tmp_path/'response.txt').read_text()==response
        original(path,ident,response)
    monkeypatch.setattr(m,'finish',finish)
    call(tmp_path,lambda:SimpleNamespace(text='exact raw'))

def test_late_response_survives_interrupted_ledger(tmp_path):
    def invoke():
        with sqlite3.connect(tmp_path/'a.db') as c:c.execute("update model_attempts set state='INTERRUPTED'")
        return SimpleNamespace(text='late raw')
    with pytest.raises(PermissionError,match='complete'):call(tmp_path,invoke)
    assert (tmp_path/'response.txt').read_text()=='late raw'
    assert m.reconcile(tmp_path/'a.db','w')['consumed_ordinals']==[1]

def test_failure_after_response_does_not_allow_redispatch(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'finish',lambda *a:(_ for _ in ()).throw(RuntimeError('checkpoint failure')))
    with pytest.raises(RuntimeError):call(tmp_path,lambda:SimpleNamespace(text='saved'))
    with pytest.raises(PermissionError):call(tmp_path,lambda:pytest.fail('redispatched'))
    assert (tmp_path/'response.txt').read_text()=='saved'

def test_existing_two_consumed_attempts_allow_only_third(tmp_path):
    for ordinal in (1,2):
        m.start(tmp_path/'a.db','w',ordinal,{},'p','m');m.reconcile(tmp_path/'a.db','w')
    call(tmp_path,lambda:SimpleNamespace(text='third'),3)
    with pytest.raises(PermissionError):call(tmp_path,lambda:pytest.fail('fourth'),4)

def test_kernel_lock_releases_after_child_exit(tmp_path):
    import subprocess,sys,os
    p=subprocess.run([sys.executable,'-c',"from governance.model_attempts import request_lock,start; import os; from pathlib import Path; p=Path(__import__('sys').argv[1]);\nwith request_lock(p):\n start(p,'w',1,{},'p','m'); os._exit(7)",str(tmp_path/'a.db')],env={**os.environ,'PYTHONPATH':str(__import__('pathlib').Path(m.__file__).parents[1])})
    assert p.returncode==7
    assert m.reconcile(tmp_path/'a.db','w')['consumed_ordinals']==[1]

def test_final_checkpoint_continuation_uses_only_attempt_three(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command
    from governance.brownfield_lifecycle import build
    from governance import audit_logger
    from governance.file_transaction import apply_transaction
    from test_brownfield_lifecycle import setup,S,C,T
    root=setup(tmp_path);audit_logger.DB_PATH=tmp_path/'audit.db';calls=[]
    def executor(state):
        calls.append(state['attempt'])
        originals={p:(root/p).read_bytes() for p in (S,C)}
        proposals={p:b+b'\n// fixture' for p,b in originals.items()}
        return {'source_transaction':apply_transaction(proposals,originals,root,[S,C],'w'),'proposals':proposals}
    state={'workflow_id':'w','workspace':str(root),'plan_hash':'p','scope':[S,C,T],'production_paths':[S,C],'test_path':T,'test_bytes':b'class Generated {}'}
    cfg={'configurable':{'thread_id':'w'}}
    with SqliteSaver.from_conn_string(str(tmp_path/'flow.db')) as saver:
        app=build(executor,lambda s:{'passed':False,'diagnostics':'genuine fixture compiler output'}).compile(checkpointer=saver,interrupt_before=['implement'])
        app.invoke(state,cfg);app.invoke(Command(resume='approve'),cfg)
        assert app.get_state(cfg).next==('implement',)
    with SqliteSaver.from_conn_string(str(tmp_path/'flow.db')) as saver:
        app=build(executor,lambda s:{'passed':False,'diagnostics':'fixture compiler failure'}).compile(checkpointer=saver)
        old=app.get_state(cfg).values
        app.update_state(cfg,{**old,'attempt':2},as_node='approval')
        result=app.invoke(None,cfg)
        assert result['status']=='STOPPED_RETRY_EXHAUSTED'
        assert calls==[3] and not app.get_state(cfg).next
        assert not (root/T).exists()
        assert all(not (root/p).read_bytes().endswith(b'// fixture') for p in (S,C))
