"""Seeded policy fixtures plus read-only retrospective readiness verification.

No live plan approval, policy exception approval, workflow resume or deployment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from governance import audit_logger
from governance.policy_engine import PolicyViolation,PolicyApprovalRequired,load_policy
from governance.file_transaction import apply_transaction,finish_transaction
from governance.documentation_release import current,evaluate
from governance.semantic_plan import digest

SOURCE='url-shortener/src/main/java/url_shortener/PolicyProbe.java'
CASES={
 'secret':b'class PolicyProbe { String password="seeded-not-a-real-secret"; }',
 'process':b'class PolicyProbe { void probe() { Runtime.getRuntime().exec("id"); } }',
 'sql':b'class PolicyProbe { String sql="SELECT * FROM users WHERE id="+input; }',
 'pii_log':b'class PolicyProbe { void probe(){ logger.info("email {}", email); } }',
 'dependency':b'import unknown.library.Client; class PolicyProbe {}',
 'test_disable':b'class PolicyProbe { @Disabled void probe(){} }',
 'reviewable_process':b'class PolicyProbe { ProcessBuilder probe(){ return new ProcessBuilder("/usr/bin/true"); } }',
}


def dump(path,obj):path.write_text(json.dumps(obj,indent=2)+'\n')


if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
 parser.add_argument('--retained-workflow',type=Path,required=True);args=parser.parse_args()
 base=args.directory.resolve();base.mkdir(exist_ok=False);old=args.retained_workflow.resolve()
 audit_logger.DB_PATH=base/'audit.db'
 with sqlite3.connect(old/'audit.db') as src,sqlite3.connect(base/'audit.db') as dest:src.backup(dest)
 fingerprints={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for db in (old/'audit.db',old/'workflow.db') for p in (db,Path(str(db)+'-wal')) if p.exists()}
 state=json.loads((old/'result.json').read_text())['integrated_result']
 verified=current(state);readiness=evaluate(state)['readiness']
 assert readiness['checks']['static_security_compliance_controls'] and readiness['checks']['protected_inventory_and_tests']
 assert readiness['checks']['operational_validation_evidence'] and readiness['status']=='BLOCKED'
 assert not readiness['checks']['human_security_signoff'] and not readiness['checks']['human_operational_signoff']
 assert fingerprints=={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in fingerprints}
 dump(base/'readiness.json',readiness);dump(base/'historical-integrity.json',{'unchanged':True,'sha256':fingerprints,'verified_bundle_hash':digest(verified)})
 root=base/'fixture-workspace';target=root/SOURCE;target.parent.mkdir(parents=True);original=b'class PolicyProbe { int value=1; }';target.write_bytes(original)
 results=[]
 for name,data in CASES.items():
  try:apply_transaction({SOURCE:data},{SOURCE:original},root,[SOURCE],'policy-fixture-'+name)
  except PolicyViolation as error:
   assert target.read_bytes()==original
   results.append({'case':name,'outcome':'approval_required' if isinstance(error,PolicyApprovalRequired) else 'denied',
                   'zero_source_writes':True,'policy_report':error.policy_report})
  else:raise AssertionError('Seeded risk was applied')
 legitimate=original.replace(b'value=1',b'value=2')
 tx=apply_transaction({SOURCE:legitimate},{SOURCE:original},root,[SOURCE],'policy-fixture-legitimate')
 assert target.read_bytes()==legitimate
 finish_transaction(tx['transaction_id'],root,[SOURCE],passed=False);assert target.read_bytes()==original
 results.append({'case':'legitimate','outcome':'allowed_then_fixture_cleanup_rollback','transaction_id':tx['transaction_id']})
 dump(base/'scenarios.json',results)
 with sqlite3.connect(base/'audit.db') as conn:
  rows=[{'id':i,'timestamp':t,'workflow_id':w,'agent':a,'event':e,'details':json.loads(d)} for i,t,w,a,e,d in conn.execute('select id,timestamp,workflow_id,agent,event,details from audit_events order by id')]
 dump(base/'audit.json',rows);dump(base/'new-policy-events.json',[r for r in rows if r['agent']=='policy_engine'])
 dump(base/'summary.json',{'provenance':'Seeded transaction fixtures; retrospective readiness on independently verified retained live evidence using copied audit authority',
      'policy_hash':digest(load_policy()),'scenario_count':len(results),'high_risk_approval_granted':False,
      'human_security_signoff':False,'human_operational_signoff':False,'readiness':readiness['status'],
      'historical_decisions_unchanged':True,'live_workflow_resumed':False,'deployment_authorized':False})
 print(json.dumps({'status':readiness['status'],'scenarios':len(results),'policy_hash':digest(load_policy())},indent=2))
