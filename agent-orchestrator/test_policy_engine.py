"""Synthetic risk/approval fixtures; never organizational sign-off evidence."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.file_transaction import apply_transaction,finish_transaction
from governance.policy_engine import evaluate,enforce,PolicyViolation,PolicyApprovalRequired,build_review_workflow

SOURCE='url-shortener/src/main/java/url_shortener/PolicyDemo.java'
TEST='url-shortener/src/test/java/url_shortener/OriginalTest.java'
OLD=b'package url_shortener; class PolicyDemo { int value=1; }'


def setup(tmp_path,monkeypatch):
    root=tmp_path/'workspace';target=root/SOURCE;target.parent.mkdir(parents=True);target.write_bytes(OLD)
    monkeypatch.setattr(audit_logger,'DB_PATH',tmp_path/'audit.db')
    return root


def events():
    with sqlite3.connect(audit_logger.DB_PATH) as c:
        return [(e,json.loads(d)) for e,d in c.execute('select event,details from audit_events order by id')]


@pytest.mark.parametrize('seed,rule',[
 ('String password="seeded-secret-value";','SEC-SECRETS'),
 ('String apiKey="not-a-real-api-key";','SEC-SECRETS'),
 ('String token="AKIAABCDEFGHIJKLMNOP";','SEC-SECRETS'),
 ('new ProcessBuilder("sh","-c","id").start();','SEC-PROCESS'),
 ('Runtime.getRuntime().exec("id");','SEC-PROCESS'),
 ('Class.forName("evil.Code");','SEC-PROCESS'),
 ('String sql="SELECT * FROM users WHERE id=" + userInput;','SEC-SQL'),
 ('entityManager.createNativeQuery("SELECT x"+input);','SEC-SQL'),
 ('logger.info("email {}", user.email);','COM-PII-LOG'),
 ('logger.info("fixture.person@example.invalid");','COM-PII-LOG'),
 ('logger.info("123-45-6789");','COM-PII-LOG'),
 ('String token="seeded-token-value";','SEC-SECRETS'),
 ('System.out.println(originalUrl);','COM-PII-LOG'),
 ('import evil.dependency.Client;','SEC-DEPENDENCY-ALLOWLIST'),
 ('evil.dependency.Client.send();','SEC-DEPENDENCY-ALLOWLIST'),
 ('@Disabled void test() {}','TEST-DISABLE'),
 ('assertTrue(true);','TEST-DISABLE'),
 ('new ProcessBuilder("/usr/bin/true").command("sh");','SEC-PROCESS'),
])
def test_seeded_violations_are_denied_before_source_checkpoint_or_write(tmp_path,monkeypatch,seed,rule):
    root=setup(tmp_path,monkeypatch);new=('package url_shortener; class PolicyDemo { '+seed+' }').encode()
    with pytest.raises(PolicyViolation) as error:apply_transaction({SOURCE:new},{SOURCE:OLD},root,[SOURCE],'w')
    assert error.value.policy_report['decision']=='deny'
    assert rule in [f['policy_id'] for f in error.value.policy_report['findings']]
    assert (root/SOURCE).read_bytes()==OLD
    assert not list((root/'.agent-transactions').glob('*.json'))
    assert not any(e=='FILE_CHECKPOINT_CREATED' for e,_ in events())
    assert 'seeded-secret-value' not in json.dumps(events())


@pytest.mark.parametrize('path',['url-shortener/pom.xml','url-shortener/src/main/resources/application.properties',
                                  'url-shortener/.mvn/jvm.config','../escape.java',TEST])
def test_protected_configuration_and_existing_tests_cannot_be_edited(tmp_path,monkeypatch,path):
    root=setup(tmp_path,monkeypatch)
    with pytest.raises(PolicyViolation):apply_transaction({path:b'weakened'},{path:b'original'},root,[path],'w')
    assert (root/SOURCE).read_bytes()==OLD


def test_legitimate_proposal_and_additive_tests_pass(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch);new=OLD.replace(b'value=1',b'value=2')
    tx=apply_transaction({SOURCE:new},{SOURCE:OLD},root,[SOURCE],'w')
    assert (root/SOURCE).read_bytes()==new
    finish_transaction(tx['transaction_id'],root,[SOURCE],passed=False)
    assert (root/SOURCE).read_bytes()==OLD
    test=b'import org.junit.jupiter.api.Test; class BoundaryTest { @Test void boundary(){ org.junit.jupiter.api.Assertions.assertEquals(2, 1+1); } }'
    result=enforce({TEST:test},{TEST:None},root,'w',allow_new_test_files=[TEST])
    assert result['decision']=='allow'
    with pytest.raises(PolicyViolation):enforce({TEST:test},{TEST:None},root,'w')


def reviewed(root,tmp_path,decision='approve'):
    data=b'package url_shortener; class PolicyDemo { ProcessBuilder probe(){ return new ProcessBuilder("/usr/bin/true"); } }'
    state={'workspace':str(root),'workflow_id':'w','proposals':{SOURCE:data},'originals':{SOURCE:OLD}}
    with SqliteSaver.from_conn_string(str(tmp_path/'review.db')) as saver:
        app=build_review_workflow().compile(checkpointer=saver);config={'configurable':{'thread_id':'policy-review'}}
        result=app.invoke(state,config)
        assert result['__interrupt__'][0].value['type']=='POLICY_EXCEPTION_APPROVAL'
        assert (root/SOURCE).read_bytes()==OLD
        result=app.invoke(Command(resume=decision),config)
    return data,result


def test_supported_high_risk_requires_explicit_hash_bound_policy_approval(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch)
    data,result=reviewed(root,tmp_path)
    with pytest.raises(PolicyApprovalRequired):apply_transaction({SOURCE:data},{SOURCE:OLD},root,[SOURCE],'w')
    receipt=result['policy_approval']
    tx=apply_transaction({SOURCE:data},{SOURCE:OLD},root,[SOURCE],'w',policy_approvals=[receipt])
    assert (root/SOURCE).read_bytes()==data
    assert any(e=='POLICY_EXCEPTION_APPROVED' and d['approval_reference']==receipt for e,d in events())
    finish_transaction(tx['transaction_id'],root,[SOURCE],passed=False)


@pytest.mark.parametrize('attack',['proposal','original','workflow','workspace','phase','receipt','database','database_body','database_decision','policy'])
def test_tampered_policy_evidence_or_approval_cannot_be_reused(tmp_path,monkeypatch,attack):
    from governance import policy_engine
    root=setup(tmp_path,monkeypatch);data,state=reviewed(root,tmp_path);receipt=state['policy_approval'];original=OLD;wid='w';phase='pre_write'
    if attack=='proposal':data+=b' // changed'
    elif attack=='original':original+=b' '
    elif attack=='workflow':wid='other'
    elif attack=='workspace':root=tmp_path/'other';root.mkdir()
    elif attack=='phase':phase='release'
    elif attack=='receipt':receipt={**receipt,'evidence_hash':'tampered'}
    elif attack=='database':
        with sqlite3.connect(audit_logger.DB_PATH) as c:c.execute("delete from audit_events where event='POLICY_EXCEPTION_APPROVED'")
    elif attack in {'database_body','database_decision'}:
        with sqlite3.connect(audit_logger.DB_PATH) as c:
            row=c.execute("select id,details from audit_events where event='POLICY_EXCEPTION_APPROVED'").fetchone();payload=json.loads(row[1])
            if attack=='database_body':payload['evidence_body']['files'][SOURCE]['proposed_hash']='tampered'
            else:payload['decision']='allow'
            c.execute('update audit_events set details=? where id=?',(json.dumps(payload),row[0]))
    elif attack=='policy':
        p=tmp_path/'policy.json';policy=json.loads(policy_engine.POLICY.read_text());policy['allowed_import_prefixes'].append('approved.other.');p.write_text(json.dumps(policy));monkeypatch.setattr(policy_engine,'POLICY',p)
    with pytest.raises(PolicyApprovalRequired):enforce({SOURCE:data},{SOURCE:original},root,wid,phase=phase,approvals=[receipt])


def test_rejection_and_unsupported_exceptions_cannot_be_overridden(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch);data,result=reviewed(root,tmp_path,'reject')
    assert result['status']=='STOPPED_POLICY_REJECTED'
    with pytest.raises(PolicyApprovalRequired):enforce({SOURCE:data},{SOURCE:OLD},root,'w')
    bad=b'new ProcessBuilder("sh");'
    with SqliteSaver.from_conn_string(str(tmp_path/'denied.db')) as saver:
        app=build_review_workflow().compile(checkpointer=saver)
        with pytest.raises(PermissionError,match='Only supported'):
            app.invoke({'proposals':{SOURCE:bad},'originals':{SOURCE:OLD},'workspace':str(root),'workflow_id':'w'},
                       {'configurable':{'thread_id':'deny'}})


def test_missing_or_invalid_policy_fails_closed_and_is_audited(tmp_path,monkeypatch):
    from governance import policy_engine
    root=setup(tmp_path,monkeypatch);bad=tmp_path/'bad.json';bad.write_text('{}');monkeypatch.setattr(policy_engine,'POLICY',bad)
    with pytest.raises(PermissionError):apply_transaction({SOURCE:OLD+b' '},{SOURCE:OLD},root,[SOURCE],'w')
    assert (root/SOURCE).read_bytes()==OLD
    assert any(e=='POLICY_EVALUATION_ERROR' for e,_ in events())


def test_readiness_cannot_turn_technical_controls_into_human_signoffs(tmp_path,monkeypatch):
    from governance.documentation_release import evaluate as readiness
    from test_documentation_release import manifest,state
    root=setup(tmp_path,monkeypatch);m=manifest(tmp_path);s=state(tmp_path,m)
    with patch('governance.documentation_release.current',return_value=m),patch('governance.policy_engine.release_controls',return_value={
        'static_security_compliance_controls':True,'protected_inventory_and_tests':True,'operational_validation_evidence':True,
        'policy_evidence_hash':'verified','policy_hash':'v1'}):
        result=readiness(s)['readiness']
    assert result['checks']['static_security_compliance_controls']
    assert not result['checks']['human_security_signoff'] and not result['checks']['human_operational_signoff']
    assert result['status']=='BLOCKED' and not result['deployment_authorized']


def test_existing_wildcard_persistence_and_hibernate_url_annotation_are_allowed(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch)
    new=b'import jakarta.persistence.*;\nimport org.hibernate.validator.constraints.URL;\nclass PolicyDemo { @URL String url; }'
    assert enforce({SOURCE:new},{SOURCE:OLD},root,'w')['decision']=='allow'


@pytest.mark.parametrize('data',[
    br'Runtime.get\u0052untime().exec("id");',
    b'String sql = new StringBuilder("SELECT * FROM users WHERE id=").append(input).toString();',
    b'',
])
def test_obfuscated_sql_builder_and_empty_source_fail_closed(tmp_path,monkeypatch,data):
    root=setup(tmp_path,monkeypatch)
    with pytest.raises(PolicyViolation):apply_transaction({SOURCE:data},{SOURCE:OLD},root,[SOURCE],'w')
    assert (root/SOURCE).read_bytes()==OLD


def test_parameterized_sql_and_configuration_loaded_secret_are_permitted(tmp_path,monkeypatch):
    root=setup(tmp_path,monkeypatch)
    data=b'class PolicyDemo { void query(){ connection.prepareStatement("SELECT * FROM users WHERE id = ?"); String password = config.getPassword(); } }'
    assert enforce({SOURCE:data},{SOURCE:OLD},root,'w')['decision']=='allow'


def test_later_rejection_of_exact_exception_invalidates_earlier_approval(tmp_path,monkeypatch):
    from governance.policy_engine import audit
    root=setup(tmp_path,monkeypatch);data,state=reviewed(root,tmp_path)
    report=evaluate({SOURCE:data},{SOURCE:OLD},root,'w')
    audit(report,'POLICY_EXCEPTION_REJECTED')
    with pytest.raises(PolicyApprovalRequired):enforce({SOURCE:data},{SOURCE:OLD},root,'w',approvals=[state['policy_approval']])
    assert (root/SOURCE).read_bytes()==OLD


def test_legacy_repair_write_is_guarded_and_uses_actual_workflow_lineage(tmp_path,monkeypatch):
    from governance.repair_executor import apply_approved_repair
    from governance.policy_engine import workflow_policy_context
    root=setup(tmp_path,monkeypatch);path='url-shortener/src/main/java/url_shortener/ShortUrl.java'
    target=root/path;target.write_text('class ShortUrl { int value=1; }')
    original=target.read_bytes()
    with workflow_policy_context('repair-workflow'):
        with pytest.raises(PolicyViolation):apply_approved_repair({'path':path,'old_text':'int value=1;',
                'new_text':'String password="seeded-risk";'},root)
    assert target.read_bytes()==original
    with sqlite3.connect(audit_logger.DB_PATH) as c:
        assert c.execute("select workflow_id from audit_events where event='POLICY_EVALUATED'").fetchone()[0]=='repair-workflow'


def test_repair_preserves_source_changed_during_policy_evaluation(tmp_path,monkeypatch):
    from governance import policy_engine
    from governance.repair_executor import apply_approved_repair
    root=setup(tmp_path,monkeypatch);path='url-shortener/src/main/java/url_shortener/ShortUrl.java';target=root/path
    target.write_text('class ShortUrl { int value=1; }');original_enforce=policy_engine.enforce
    def concurrent(*args,**kwargs):
        result=original_enforce(*args,**kwargs);target.write_text('external change');return result
    monkeypatch.setattr(policy_engine,'enforce',concurrent)
    with pytest.raises(RuntimeError,match='changed'):
        apply_approved_repair({'path':path,'old_text':'value=1','new_text':'value=2'},root)
    assert target.read_text()=='external change'


@pytest.mark.parametrize('attack',['secret','existing_test','build'])
def test_release_gate_rescans_bytes_and_rejects_stale_technical_pass(tmp_path,monkeypatch,attack):
    from governance.policy_engine import release_controls
    from governance.test_safety import preserve
    root=setup(tmp_path,monkeypatch);test=root/TEST;test.parent.mkdir(parents=True);test.write_bytes(b'class OriginalTest {}')
    build=root/'url-shortener/pom.xml';build.write_bytes(b'original build')
    evidence=tmp_path/'evidence';evidence.mkdir();baseline=preserve(root,evidence/'originals')
    new='url-shortener/src/test/java/url_shortener/NewTest.java';(root/new).write_bytes(b'class NewTest {}')
    (root/SOURCE).write_bytes(OLD.replace(b'value=1',b'value=2'))
    state={'origin':{'evidence':str(evidence),'baseline_hash':baseline,'scope':{'production_file':SOURCE,'new_test_file':new}},'run_id':'w'}
    manifest={'workspace':str(root),'records':{'positive':{'exit_code':0,'cases':{'original':'passed','new':'passed'}}}}
    state['policy_evidence']=release_controls(state,manifest)
    assert state['policy_evidence']['static_security_compliance_controls']
    if attack=='secret':(root/SOURCE).write_bytes(b'class PolicyDemo { String token="seeded-secret"; }')
    elif attack=='existing_test':test.write_bytes(b'weakened test')
    else:build.write_bytes(b'skipTests=true')
    with pytest.raises(PermissionError):release_controls(state,manifest)
