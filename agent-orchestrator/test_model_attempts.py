import sqlite3
import pytest
from governance.model_attempts import start,finish,fail,reconcile
def test_started_attempt_is_durable_and_counts_after_interruption(tmp_path):
 db=tmp_path/'a.db';ident=start(db,'w',1,{'receipt':'fresh'},'prompt','ollama')
 state=reconcile(db,'w');assert state['consumed_ordinals']==[1];assert state['interrupted'][0]['attempt_id']==ident;assert state['next_ordinal']==2
 with pytest.raises(PermissionError):start(db,'w',1,{},'again','ollama')
def test_complete_and_failed_attempts_are_distinct(tmp_path):
 db=tmp_path/'a.db';a=start(db,'w',1,{},'one','ollama');finish(db,a,'response');b=start(db,'w',2,{},'two','ollama');fail(db,b,RuntimeError())
 assert reconcile(db,'w')['interrupted']==[]
def test_no_dispatch_has_no_consumed_attempt(tmp_path):
 assert reconcile(tmp_path/'a.db','w')=={'consumed_ordinals':[],'interrupted':[],'next_ordinal':1}
