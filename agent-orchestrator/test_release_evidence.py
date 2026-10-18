"""Portable synthetic report fixtures exercise the verifier, not live Maven claims."""
import base64,copy,json,shutil,sqlite3
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET
import pytest
from agents.test_generation_agent import CASES,render
from governance.semantic_plan import custom_expiration_contract,digest
from governance.test_stage import scope_hash
from governance.test_safety import preserve,sha
from governance.release_evidence import verify
from governance.documentation_release import current


@pytest.fixture
def origin(tmp_path):
    repo=Path(__file__).resolve().parents[1]
    journal=json.loads((repo/'docs/evidence/test-generation/positive-source-journals.json').read_text())[0]
    entry=journal['files'][0];old=base64.b64decode(entry['original_content']);new=base64.b64decode(entry['expected_content'])
    workspace=tmp_path/'workspace';workspace.mkdir();evidence=tmp_path/'evidence';evidence.mkdir()
    source=entry['path'];test='url-shortener/src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java'
    for name,data in [(source,old),('url-shortener/src/test/java/url_shortener/UrlExpirationIntegrationTests.java',
              (repo/'url-shortener/src/test/java/url_shortener/UrlExpirationIntegrationTests.java').read_bytes())]:
        p=workspace/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    baseline_hash=preserve(workspace,evidence/'originals')
    generated=render({'cases':[{'obligation_id':k,'spec':v} for k,v in CASES.items()]},custom_expiration_contract())
    (workspace/source).write_bytes(new);(workspace/test).write_bytes(generated)
    context={'workflow_id':'test-origin','workspace':str(workspace),'requirements_hash':'req','requirement_version':1,
             'approved_design_hash':'design','approved_contract_hash':'contract','structured_requirements':{},
             'clarification_answers':{'A1':'Custom expiration timestamp'},'design_artifact':{},'obligation_contract':custom_expiration_contract()}
    state={'context':context,'status':'AWAITING_TEST_RESULTS_REVIEW','scope':{'production_file':source,'new_test_file':test},
           'old_source':old,'new_source':new,'evidence':str(evidence),'baseline_hash':baseline_hash,
           'test_hash':sha(generated),'source_transaction':journal['transaction_id']}
    state['approved_scope_hash']=scope_hash(state)
    (workspace/'.agent-transactions').mkdir();journal['workflow_id']=context['workflow_id']
    (workspace/'.agent-transactions'/(state['source_transaction']+'.json')).write_text(json.dumps(journal))
    (evidence/'test-journal.json').write_text(json.dumps({'status':'validated','scope_hash':state['approved_scope_hash'],
       'test_hash':state['test_hash'],'baseline_hash':baseline_hash,'source_transaction_id':state['source_transaction']}))
    (evidence/'generation-prompt.txt').write_text('fixture prompt');(evidence/'generation-response.json').write_text(json.dumps({'cases':[{'obligation_id':k,'spec':v} for k,v in CASES.items()]}))
    baseline={'Original#test':'passed'}
    generated_cases={'url_shortener.GeneratedExpirationBoundaryTest#'+n:'passed' for n in ('futureExpiration','pastExpiration','equalNowExpiration','missingExpiration')}
    records={}
    for phase in ('baseline','negative','positive'):
        folder=evidence/phase;folder.mkdir();cases={**baseline,**({} if phase=='baseline' else generated_cases)}
        if phase=='negative':cases['url_shortener.GeneratedExpirationBoundaryTest#equalNowExpiration']='failure'
        suite=ET.Element('testsuite')
        for key,status in cases.items():
            cls,name=key.split('#');case=ET.SubElement(suite,'testcase',classname=cls,name=name)
            if status=='failure':ET.SubElement(case,'failure',type='org.opentest4j.AssertionFailedError')
        ET.ElementTree(suite).write(folder/'TEST-fixture.xml')
        record={'cases':cases,'exit_code':1 if phase=='negative' else 0,'command':['./mvnw','-o','-Dmaven.repo.local=fixture','clean','test']}
        (folder/'result.json').write_text(json.dumps(record));(folder/'maven.log').write_text('Synthetic fixture execution record')
        records[phase]=record
    state['validation']=records['positive'];state['negative_validation']=records['negative']
    database=tmp_path/'audit.db';state['origin_audit_db']=str(database)
    with sqlite3.connect(database) as c:
        c.execute('create table audit_events(id integer primary key,workflow_id text,event text,details text)')
        for event,details in [('TEST_SCOPE_APPROVED',{'scope_hash':state['approved_scope_hash']}),
                             ('TEST_STAGE_EXIT_PASSED',{'scope_hash':state['approved_scope_hash'],'validation':state['validation'],'baseline_hash':baseline_hash,'test_hash':state['test_hash']})]:
            c.execute('insert into audit_events(workflow_id,event,details) values(?,?,?)',('test-origin',event,json.dumps(details)))
    return state


@pytest.mark.parametrize('attack',['source','test','xml','logs','audit','journal','baseline','spec','exit_hash'])
def test_evidence_tampering_fails_closed(origin,attack):
    s=origin;w=Path(s['context']['workspace']);e=Path(s['evidence'])
    if attack=='source':(w/s['scope']['production_file']).write_text('different implementation')
    if attack=='test':(w/s['scope']['new_test_file']).write_text('weakened test')
    if attack=='xml':(e/'positive/TEST-fixture.xml').write_text('<testsuite/>')
    if attack=='logs':(e/'positive/maven.log').unlink()
    if attack=='audit':
        with sqlite3.connect(s['origin_audit_db']) as c:c.execute("delete from audit_events where event='TEST_SCOPE_APPROVED'")
    if attack=='journal':(w/'.agent-transactions'/(s['source_transaction']+'.json')).write_text('{}')
    if attack=='baseline':(e/'originals/baseline.json').write_text('{}')
    if attack=='spec':(e/'generation-response.json').write_text('{"cases":[],"java":"disabled"}')
    if attack=='exit_hash':
        with sqlite3.connect(s['origin_audit_db']) as c:c.execute("update audit_events set details='{}' where event='TEST_STAGE_EXIT_PASSED'")
    with patch('governance.release_evidence.assert_design_current'):
        with pytest.raises((PermissionError,OSError,ValueError,KeyError)):verify(s)


def test_byte_identical_foreign_workspace_invalidates_old_review(origin,tmp_path):
    with patch('governance.release_evidence.assert_design_current'):original_manifest=verify(origin)
    new=tmp_path/'different-workspace';shutil.copytree(origin['context']['workspace'],new)
    changed=copy.deepcopy(origin);changed['context']['workspace']=str(new)
    with patch('governance.release_evidence.assert_design_current'):
        assert digest(verify(changed))!=digest(original_manifest)
        with pytest.raises(PermissionError):current({'origin':changed,'bundle_hash':digest(original_manifest)})


def test_limited_test_scope_never_becomes_engineering_plan_approval(origin):
    with patch('governance.release_evidence.assert_design_current'):
        assert verify(origin)['approved_implementation_plan_hash'] is None
        origin['engineering_test_link']={'plan_hash':'claim-based-on-limited-scope'}
        with pytest.raises(PermissionError,match='no engineering execution receipt'):verify(origin)
