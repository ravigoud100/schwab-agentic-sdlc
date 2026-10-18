import base64, difflib, hashlib, json, os, shutil, subprocess, time, xml.etree.ElementTree as ET
from pathlib import Path
repo=Path('/Users/ruwicc/Projects/schwab-agentic-sdlc')
history=Path('/private/tmp/schwab-assessment-brownfield-ledger')
evidence=repo/'docs/evidence/assessment-scenarios/brownfield-final-attempt'
out=Path('/private/tmp/schwab-brownfield-offline-validation')
out.mkdir(exist_ok=False)
def sha(b):return hashlib.sha256(b).hexdigest()
def hashes(root):return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
def save(path,data):path.write_text(json.dumps(data,indent=2))
before=hashes(history);save(out/'historical-before.json',before)
baseline=out/'baseline';baseline.mkdir()
shutil.copytree(history/'workspace/url-shortener',baseline/'url-shortener',ignore=shutil.ignore_patterns('target'))
baseline_hashes=hashes(baseline)
expected=json.loads((history/'pre-attempt-3-backup/workspace-hashes.json').read_text())
assert all(baseline_hashes[p]==h for p,h in expected.items() if p.startswith('url-shortener/'))
raw=(evidence/'response.txt').read_text();payload=json.loads(raw.strip().removeprefix('```json').removesuffix('```'))
props={p['path']:p['content'].encode() for p in payload['files']}
journal=[j for j in json.loads((evidence/'journals.json').read_text()) if 'files' in j][0]
for f in journal['files']:
 assert sha(props[f['path']])==f['expected_current_hash']
 assert props[f['path']]==base64.b64decode(f['expected_content'])
 assert (baseline/f['path']).read_bytes()==base64.b64decode(f['original_content'])
testpath='url-shortener/src/test/java/url_shortener/UrlStatusIntegrationTest.java'
test=(evidence/'UrlStatusIntegrationTest.java').read_bytes()
def diff(a,b,name):return ''.join(difflib.unified_diff(a.decode().splitlines(True),b.decode().splitlines(True),fromfile='a/'+name,tofile='b/'+name))
(out/'original-proposal.diff').write_text(''.join(diff((baseline/p).read_bytes(),v,p) for p,v in props.items()))
(out/'test-harness.diff').write_text(diff(b'',test,testpath))
def run(label,changes):
 d=out/label;d.mkdir();shutil.copytree(baseline/'url-shortener',d/'url-shortener')
 for p,b in changes.items():(d/p).write_bytes(b)
 (d/testpath).write_bytes(test)
 command=['./mvnw','-o','-Dmaven.repo.local=/private/tmp/schwab-test-generation/maven-cache','clean','test']
 start=time.monotonic()
 with (d/'maven.log').open('w') as log:
  child=subprocess.Popen(command,cwd=d/'url-shortener',stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'JAVA_HOME':'/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home'})
  save(d/'process.json',{'pid':child.pid,'command':command});code=child.wait()
 cases=[]
 for p in (d/'url-shortener/target/surefire-reports').glob('TEST-*.xml'):
  for c in ET.parse(p).getroot().iter('testcase'):
   cases.append({'class':c.get('classname'),'name':c.get('name'),'failure':c.find('failure') is not None,'error':c.find('error') is not None,'skipped':c.find('skipped') is not None})
 result={'exit_code':code,'elapsed_seconds':time.monotonic()-start,'tests':len(cases),'failures':sum(c['failure'] for c in cases),'errors':sum(c['error'] for c in cases),'skips':sum(c['skipped'] for c in cases),'cases':cases}
 save(d/'result.json',result);print(label,json.dumps(result),flush=True)
 for p,h in baseline_hashes.items():
  if p not in changes:assert sha((d/p).read_bytes())==h
 return result
original=run('original',props)
controller='url-shortener/src/main/java/url_shortener/UrlController.java'
assert original['exit_code']!=0
assert 'symbol:   variable Instant' in (out/'original/maven.log').read_text()
corrected=dict(props)
assert corrected[controller].count(b'import java.net.URI;')==1
corrected[controller]=corrected[controller].replace(b'import java.net.URI;',b'import java.net.URI;\nimport java.time.Instant;',1)
(out/'compilation-correction.diff').write_text(diff(props[controller],corrected[controller],controller))
(out/'corrected-proposal.diff').write_text(''.join(diff((baseline/p).read_bytes(),v,p) for p,v in corrected.items()))
fixed=run('corrected',corrected)
after=hashes(history);assert after==before
save(out/'historical-after.json',after)
save(out/'summary.json',{'historical_files_unchanged':True,'response_hash':sha(raw.encode()),'proposal_matches_journal':True,'original':original,'corrected':fixed,'correction_provenance':'Assistant-authored deterministic import addition; no Ollama request or AI recovery','correction':'Add import java.time.Instant to retained controller in disposable corrected copy only','test_hash':sha(test),'historical_budget_reset':False})
