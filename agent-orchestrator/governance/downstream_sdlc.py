"""Shared downstream SDLC stages: test generation, Maven validation, docs, readiness, review.

Provides reusable downstream execution for sequential (integrated-expiration-v1) and
parallel (parallel-expiration-v1) workflows. Preserves transaction isolation, whole-set
rollback, test scope approval, test results review, and final release review interrupts.
"""
import base64
import difflib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import xml.etree.ElementTree as ET

from langgraph.types import interrupt
from agents.test_generation_agent import generate_spec, render, CASES, validate_spec
from governance import audit_logger
from governance.audit_logger import log_event
from governance.plan_lineage import approved, before_execution, link_tests, verify_test_link, source_journal, events, receipt
from governance.semantic_plan import digest
from governance.test_safety import inventory, preserve, load_baseline, assert_integrity, new_test_target, sha
from governance.file_transaction import finish_transaction, save_journal, sync_directory
from governance.test_stage import maven, require_results

TEST_PATH = 'url-shortener/src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java'


def is_parallel(state):
    profile = state.get('profile', '')
    if profile.startswith('integrated-'):
        return False
    if profile.startswith('parallel-'):
        return True
    if 'binding' in state and 'waves' in state:
        return True
    return False


def scope(state):
    c = state['context']; root = Path(c['workspace'])
    from governance.feature_registry import get_feature_adapter
    adapter = get_feature_adapter(c.get('obligation_contract') or c)
    test_path = adapter.test_path
    if is_parallel(state):
        from governance.parallel_execution import recheck, assert_receipts
        target = root / test_path
        test_applied = {test_path: target.read_bytes()} if target.exists() and state.get('test_hash') else None
        recheck(state, applied=test_applied)
        assert_receipts(state)
        tx_ids = [tx['transaction_id'] for tx in state.get('transactions', [])]
        exec_hash = digest({'transactions': tx_ids, 'checkpoints': state.get('task_checkpoints', {})})
        return {
            'binding_hash': digest(state['binding']),
            'test_path': test_path,
            'transactions': tx_ids,
            'execution_hash': exec_hash,
            'policy': adapter.policy_description,
        }
    auth = approved(c)
    if auth['body']['test_additions'] != [test_path]:
        raise PermissionError(f'Integrated {adapter.feature_id} renderer requires its exact authorized test path')
    last_tx = state.get('transaction') or (state.get('transactions', [])[-1] if state.get('transactions') else None)
    exec_hash = last_tx['execution_receipt']['execution_hash'] if last_tx and 'execution_receipt' in last_tx else ''
    res = {
        'authorization_hash': auth['authorization_hash'],
        'test_path': test_path,
        'execution_hash': exec_hash,
        'policy': adapter.policy_description,
    }
    if state.get('transactions') and len(state['transactions']) > 1:
        res['transactions'] = [tx['transaction_id'] for tx in state['transactions']]
    return res


def assert_applied_downstream(state):
    c = state['context']; root = Path(c['workspace'])
    if is_parallel(state):
        from governance.parallel_execution import recheck, assert_receipts
        from governance.feature_registry import get_feature_adapter
        adapter = get_feature_adapter(c.get('obligation_contract') or c)
        target = root / adapter.test_path
        test_applied = {adapter.test_path: target.read_bytes()} if target.exists() and state.get('test_hash') else None
        recheck(state, applied=test_applied)
        assert_receipts(state)
        txs = state.get('transactions', [])
        if not txs:
            raise PermissionError('Parallel execution requires recorded transactions')
        for tx in txs:
            journal = source_journal(root, tx['transaction_id'])
            if journal['status'] not in {'applied', 'validated'} or journal['workflow_id'] != c['workflow_id']:
                raise PermissionError('Parallel transaction journal invalid or unapplied')
            for entry in journal['files']:
                path = entry['path']
                current_bytes = (root / path).read_bytes()
                if sha(current_bytes) != entry['expected_current_hash']:
                    raise PermissionError(f'Source file changed externally: {path}')
        return txs
    txs = state.get('transactions', [])
    if len(txs) > 1:
        for tx in txs:
            journal = source_journal(root, tx['transaction_id'])
            if journal['status'] not in {'applied', 'validated'} or journal['workflow_id'] != c['workflow_id']:
                raise PermissionError('Transaction journal invalid or unapplied')
            for entry in journal['files']:
                path = entry['path']
                current_bytes = (root / path).read_bytes()
                if sha(current_bytes) != entry['expected_current_hash']:
                    raise PermissionError(f'Source file changed externally: {path}')
        return txs
    from governance.integrated_sdlc import assert_applied
    return assert_applied(state)


def compensate(state, error):
    recovery = 'no_source_writes'
    try:
        context = state['context']; root = Path(context['workspace'])
        from governance.feature_registry import get_feature_adapter
        try:
            adapter = get_feature_adapter(context.get('obligation_contract') or context)
            test_path = adapter.test_path
        except Exception:
            test_path = TEST_PATH
        test = state.get('test_hash'); target = root / test_path
        if test and (not target.is_file() or target.is_symlink() or sha(target.read_bytes()) != test):
            raise PermissionError('Generated test changed externally; review required')
        txs = list(state.get('transactions', []))
        if not txs and state.get('transaction'):
            txs = [state['transaction']]
        if txs:
            for tx in reversed(txs):
                finish_transaction(tx['transaction_id'], root, tx['paths'], passed=False)
            if test:
                target.unlink()
                sync_directory(target.parent)
            recovery = 'rolled_back'
        journal = Path(state['evidence']) / 'integrated-test-journal.json'
        if journal.exists():
            record = json.loads(journal.read_text())
            record['status'] = recovery
            save_journal(journal, record)
    except Exception as failure:
        recovery = 'blocked: ' + str(failure)
    agent_name = 'parallel_executor' if is_parallel(state) else 'integrated_sdlc'
    event_name = 'PARALLEL_STAGE_STOPPED' if is_parallel(state) else 'INTEGRATED_STAGE_STOPPED'
    log_event(state['context']['workflow_id'], agent_name, event_name, {'error': str(error), 'recovery': recovery})
    return {**state, 'status': 'STOPPED_INTEGRATED_STAGE' if not is_parallel(state) else 'STOPPED_PARALLEL_STAGE',
            'error': str(error), 'recovery': recovery}



def test_review(state):
    try:
        assert_applied_downstream(state)
        policy = scope(state)
        if not is_parallel(state):
            if not state.get('source_validation_history') or state['source_validation_history'][-1]['transaction_id'] != state['transaction']['transaction_id']:
                raise PermissionError('Current source transaction must pass its original-test/compilation gate first')
        else:
            if not state.get('validation') or state['validation'].get('exit_code') != 0:
                raise PermissionError('Parallel combined transactions must pass compilation/tests gate first')
    except Exception as error:
        return compensate(state, error)
    hashed = digest(policy)
    decision = interrupt({'type': 'INTEGRATED_TEST_GENERATION_APPROVAL', 'scope': policy, 'scope_hash': hashed,
                          'message': 'Approve only additive constrained tests against this existing implementation transaction; no patch replay.'})
    if decision != 'approve':
        return compensate(state, 'Test-generation approval rejected')
    agent_name = 'parallel_executor' if is_parallel(state) else 'integrated_sdlc'
    log_event(state['context']['workflow_id'], agent_name, 'TEST_SCOPE_APPROVED', {'scope_hash': hashed, 'scope': policy})
    return {**state, 'scope': policy, 'approved_scope_hash': hashed, 'status': 'TEST_SCOPE_APPROVED'}


def tests(state, model, link_fn=None):
    current = state
    try:
        assert_applied_downstream(state)
        if state.get('approved_scope_hash') != digest(scope(state)):
            raise PermissionError('Test scope approval changed')
        c = state['context']
        receipt_val = {'scope_hash': state['approved_scope_hash'], 'scope': state['scope']}
        if not is_parallel(state):
            receipt(c, 'TEST_SCOPE_APPROVED', receipt_val)
        else:
            with sqlite3.connect(f"file:{Path(audit_logger.DB_PATH).resolve()}?mode=ro", uri=True) as conn:
                rows = conn.execute('SELECT details FROM audit_events WHERE workflow_id=? AND event=?',
                                    (c['workflow_id'], 'TEST_SCOPE_APPROVED')).fetchall()
            if not any(json.loads(row[0]) == receipt_val for row in rows):
                raise PermissionError('Missing exact TEST_SCOPE_APPROVED audit receipt')

        root = Path(c['workspace']); evidence = Path(state['evidence'])
        from governance.feature_registry import get_feature_adapter
        adapter = get_feature_adapter(c.get('obligation_contract') or c)
        test_path = adapter.test_path
        target = new_test_target(root, test_path)
        contract = c.get('obligation_contract')
        if contract is None:
            if adapter.feature_id == 'expiration':
                from governance.semantic_plan import custom_expiration_contract
                contract = custom_expiration_contract()
            elif adapter.feature_id == 'health-check':
                from governance.semantic_plan import health_check_contract
                contract = health_check_contract()
        ctx = {
            **c,
            'obligation_contract': contract,
            'structured_requirements': c.get('structured_requirements') or {'problem': c.get('requirement', '')},
            'clarification_answers': c.get('clarification_answers') or {'A1': 'Custom expiration timestamp'},
            'design_artifact': c.get('design_artifact') or {'components': []},
        }
        spec, prompt, raw = adapter.generate_spec(model, ctx, state['scope']); data = adapter.render(spec, contract)
        for name, content in [('generation-prompt.txt', prompt), ('generation-response.json', raw)]:
            with (evidence / name).open('x') as stream: stream.write(content)
        assert_applied_downstream(state); new_test_target(root, test_path)
        from governance.policy_engine import enforce
        enforce({test_path: data}, {test_path: None}, root, c['workflow_id'], allow_new_test_files=[test_path])

        if not is_parallel(state):
            txs = state.get('transactions', [state.get('transaction')])
            all_tx_ids = [tx['transaction_id'] for tx in txs]
            journal = {'status': 'prepared', 'test_path': test_path, 'test_hash': sha(data),
                       'scope_hash': state['approved_scope_hash'], 'source_transaction_id': all_tx_ids[-1],
                       'transactions': all_tx_ids}
        else:
            all_tx_ids = [tx['transaction_id'] for tx in state['transactions']]
            journal = {'status': 'prepared', 'test_path': test_path, 'test_hash': sha(data),
                       'scope_hash': state['approved_scope_hash'], 'source_transaction_id': all_tx_ids[-1],
                       'transactions': all_tx_ids}
        save_journal(evidence / 'integrated-test-journal.json', journal)
        fd, name = tempfile.mkstemp(prefix='.integrated-test-', dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            (link_fn or os.link)(temporary, target)
            current = {**state, 'test_hash': sha(data)}
            sync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
        journal['status'] = 'applied'; save_journal(evidence / 'integrated-test-journal.json', journal)
        agent_name = 'parallel_executor' if is_parallel(state) else 'integrated_sdlc'
        tx_ref = all_tx_ids[-1]
        log_event(c['workflow_id'], agent_name, 'TEST_FILE_CREATED', {
            'path': test_path, 'sha256': sha(data), 'scope_hash': state['approved_scope_hash'],
            'source_transaction': tx_ref})
        return {**current, 'status': 'ADDITIVE_TEST_CREATED'}
    except Exception as error:
        return compensate(current, error)


def validation(state, validator=maven):
    try:
        c = state['context']; root = Path(c['workspace']); evidence = Path(state['evidence'])
        from governance.feature_registry import get_feature_adapter
        adapter = get_feature_adapter(c.get('obligation_contract') or c)
        test_path = adapter.test_path
        if not is_parallel(state):
            txs = state.get('transactions', [state.get('transaction')])
            last_tx = txs[-1]
            execution = last_tx['execution_receipt']
            expected = {**execution['body']['expected_project_hashes'], test_path: state['test_hash']}
            if {p: sha(v) for p, v in inventory(root).items()} != expected:
                raise PermissionError('Unauthorized test/source/build changes')
        else:
            baseline_files = load_baseline(evidence / 'originals', state['baseline_hash'])
            applied = state.get('applied_changes', {})
            expected = {**{p: sha(v) for p, v in baseline_files.items()}, **{p: sha(v) for p, v in applied.items()}, test_path: state['test_hash']}
            if {p: sha(v) for p, v in inventory(root).items()} != expected:
                raise PermissionError('Unauthorized test/source/build changes')

        record = (validator or maven)(root, evidence, 'positive', state['cache'])
        adapter.validate_results(record, state['baseline_validation']['cases'])

        agent_name = 'parallel_executor' if is_parallel(state) else 'integrated_sdlc'
        log_event(c['workflow_id'], agent_name, 'TEST_STAGE_EXIT_PASSED', {
            'scope_hash': state['approved_scope_hash'], 'test_hash': state['test_hash'], 'validation': record})

        if not is_parallel(state):
            txs = state.get('transactions', [state.get('transaction')])
            for tx in txs:
                journal_path = root / '.agent-transactions' / f"{tx['transaction_id']}.json"
                if journal_path.exists() and json.loads(journal_path.read_text()).get('status') == 'validated':
                    continue
                finish_transaction(tx['transaction_id'], root, tx['paths'], passed=True)
            last_tx = txs[-1]
            link = link_tests(c, last_tx['execution_receipt'], test_path, state['test_hash'], state['approved_scope_hash'], record, transactions=txs)
            source_tx = last_tx['transaction_id']
            origin_profile = f'integrated-{adapter.feature_id}-v1'
            origin = {**state, 'profile': origin_profile, 'validation': record, 'engineering_test_link': link,
                      'source_transaction': source_tx, 'transactions': txs, 'status': 'AWAITING_TEST_RESULTS_REVIEW'}
        else:
            tx_ids = [tx['transaction_id'] for tx in state['transactions']]
            source_tx = tx_ids
            link = {
                'workflow_id': c['workflow_id'],
                'test_path': test_path,
                'test_hash': state['test_hash'],
                'scope_hash': state['approved_scope_hash'],
                'transactions': tx_ids,
                'validation': record,
                'lineage_hash': digest({
                    'binding': state['binding'],
                    'transactions': tx_ids,
                    'test_hash': state['test_hash'],
                    'scope_hash': state['approved_scope_hash'],
                })
            }
            log_event(c['workflow_id'], 'plan_lineage', 'PARALLEL_TESTS_BOUND', link)
            log_event(c['workflow_id'], 'plan_lineage', 'ENGINEERING_TESTS_BOUND', link)
            origin_profile = f'parallel-{adapter.feature_id}-v1'
            origin = {**state, 'profile': origin_profile, 'validation': record, 'engineering_test_link': link,
                      'source_transaction': source_tx, 'transactions': state['transactions'], 'status': 'AWAITING_TEST_RESULTS_REVIEW'}


        journal = Path(state['evidence']) / 'integrated-test-journal.json'
        body = json.loads(journal.read_text()); body['status'] = 'validated'; save_journal(journal, body)
        from governance.release_evidence import verify
        manifest = verify(origin)
        event_name = 'PARALLEL_EVIDENCE_VERIFIED' if is_parallel(state) else 'INTEGRATED_EVIDENCE_VERIFIED'
        log_event(c['workflow_id'], agent_name, event_name, {'bundle_hash': digest(manifest), 'lineage_hash': link['lineage_hash']})
        return {**origin, 'origin': origin, 'bundle_hash': digest(manifest), 'run_id': c['workflow_id'],
                'output_dir': state['evidence']}
    except Exception as error:
        return compensate(state, error)


def maven_record(evidence, phase, *, allow_failed=False):
    directory = Path(evidence) / phase; path = directory / 'result.json'; record = json.loads(path.read_text()); cases = {}; hashes = {}
    reports = sorted(directory.glob('TEST-*.xml'))
    if not reports and not (allow_failed and record['exit_code'] != 0):
        raise PermissionError('Missing genuine Surefire reports')
    for report in reports:
        hashes[str(report)] = sha(report.read_bytes())
        for case in ET.parse(report).getroot().iter('testcase'):
            key = case.attrib['classname'] + '#' + case.attrib['name']
            if key in cases:
                raise PermissionError('Duplicate test identity')
            cases[key] = 'failure' if case.find('failure') is not None else 'error' if case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
    if cases != record['cases']:
        raise PermissionError('Surefire/checkpoint summary mismatch')
    command = record['command']
    if len(command) != 5 or command[:2] != ['./mvnw', '-o'] or not command[2].startswith('-Dmaven.repo.local=') or command[3:] != ['clean', 'test']:
        raise PermissionError('Unsupported Maven command or exclusions')
    log_path = directory / 'maven.log'
    if not log_path.read_bytes():
        raise PermissionError('Maven execution log missing')
    for p in (path, log_path):
        hashes[str(p)] = sha(p.read_bytes())
    return record, hashes


def verify_integrated(state):
    c = state['context']; root = Path(c['workspace']); evidence = Path(state['evidence'])
    from governance.feature_registry import get_feature_adapter
    adapter = get_feature_adapter(c.get('obligation_contract') or c)
    test_path = adapter.test_path
    txs = state.get('transactions', [state.get('transaction')])
    last_tx = txs[-1]
    plan_hash = verify_test_link(c, last_tx['execution_receipt'], state['engineering_test_link'], state['source_transaction'],
                                 test_path, state['test_hash'], state['approved_scope_hash'], state['validation'],
                                 transactions=txs)
    if state['approved_scope_hash'] != digest(scope(state)):
        raise PermissionError('Test scope changed')
    receipt(c, 'TEST_SCOPE_APPROVED', {'scope_hash': state['approved_scope_hash'], 'scope': state['scope']})
    baseline = load_baseline(evidence / 'originals', state['baseline_hash'])
    if {p: sha(v) for p, v in baseline.items()} != approved(c)['body']['project_baseline']:
        raise PermissionError('Original snapshots differ from plan baseline')
    spec = json.loads((evidence / 'generation-response.json').read_text())
    contract = c.get('obligation_contract')
    if contract is None:
        if adapter.feature_id == 'expiration':
            from governance.semantic_plan import custom_expiration_contract
            contract = custom_expiration_contract()
        elif adapter.feature_id == 'health-check':
            from governance.semantic_plan import health_check_contract
            contract = health_check_contract()
    rendered = adapter.render(spec, contract)
    if rendered != (root / test_path).read_bytes():
        raise PermissionError('Generated test profile changed')

    changes = {}
    for tx in txs:
        tx_id = tx['transaction_id'] if isinstance(tx, dict) else tx
        journal = source_journal(root, tx_id)
        for e in journal['files']:
            content = base64.b64decode(e['expected_content'], validate=True)
            if sha(content) != e['expected_current_hash'] or baseline[e['path']] != base64.b64decode(e['original_content'], validate=True):
                raise PermissionError('Source snapshot/hash mismatch')
            changes[e['path']] = content

    assert_integrity(root, baseline, {**changes, test_path: rendered})
    records = {}; artifacts = {}
    for phase in ('baseline', 'positive'):
        records[phase], hashes = maven_record(evidence, phase); artifacts.update(hashes)
    if records['baseline'] != state['baseline_validation'] or records['positive'] != state['validation']:
        raise PermissionError('Maven records changed')
    if records['baseline']['exit_code'] != 0:
        raise PermissionError('Baseline validation failed')
    adapter.validate_results(records['positive'], records['baseline']['cases'])
    for item in state.get('source_validation_history', []):
        checked, hashes = maven_record(evidence, item['phase'], allow_failed=True); artifacts.update(hashes)
        if checked != item['validation']:
            raise PermissionError('Source-gate validation record changed')
    if state.get('source_validation_history'):
        last = state['source_validation_history'][-1]
        if last['transaction_id'] != state['source_transaction'] or last['validation']['exit_code'] != 0 or last['validation']['cases'] != records['baseline']['cases']:
            raise PermissionError('Current transaction has no passing original-test source gate')
    creation = json.loads((evidence / 'integrated-test-journal.json').read_text())
    if (creation.get('status') != 'validated'
            or creation.get('test_path') != test_path
            or creation.get('test_hash') != state['test_hash']
            or creation.get('scope_hash') != state['approved_scope_hash']):
        raise PermissionError('Additive test journal mismatch')
    for p in (evidence / 'integrated-test-journal.json', evidence / 'generation-prompt.txt', evidence / 'generation-response.json'):
        if p.exists():
            artifacts[str(p)] = sha(p.read_bytes())
    for tx in txs:
        tx_id = tx['transaction_id'] if isinstance(tx, dict) else tx
        p = root / '.agent-transactions' / f'{tx_id}.json'
        artifacts[str(p)] = sha(p.read_bytes())
    cutoff = receipt(c, 'ENGINEERING_TESTS_BOUND', state['engineering_test_link'])
    audit = [e for e in events(c) if e['id'] <= cutoff]
    actual_diff = ''.join(''.join(difflib.unified_diff(baseline[p].decode().splitlines(True), data.decode().splitlines(True), fromfile=p, tofile=p)) for p, data in changes.items())
    return {
        'profile': f'integrated-{adapter.feature_id}-v1', 'workflow_id': c['workflow_id'], 'workspace': str(root.resolve()),
        'requirements_hash': c['requirements_hash'], 'requirement_version': c['requirement_version'],
        'approved_design_hash': c['approved_design_hash'], 'approved_contract_hash': c['approved_contract_hash'],
        'approved_implementation_plan_hash': plan_hash, 'implementation_hash': digest({p: sha(v) for p, v in changes.items()}),
        'generated_test_hash': state['test_hash'], 'test_path': test_path, 'source_transaction': state['source_transaction'],
        'records': records, 'artifacts': artifacts, 'requirements': c['structured_requirements'],
        'clarification_answers': c['clarification_answers'], 'approved_design': c['design_artifact'],
        'approved_contract': c['obligation_contract'], 'approved_plan': c['plan'], 'actual_diff': actual_diff,
        'audit_events': audit, 'test_scope_hash': state['approved_scope_hash'],
    }


def verify_parallel(state):
    c = state['context']; root = Path(c['workspace']).resolve(strict=True)
    evidence = Path(state['evidence']).resolve(strict=True)
    from governance.parallel_execution import check_context, binding, assert_receipts
    check_context(c)
    if binding(c, state['baseline_hash']) != state['binding']:
        raise PermissionError('Parallel binding changed')
    assert_receipts(state)

    if state.get('approved_scope_hash') != digest(scope(state)):
        raise PermissionError('Test scope changed')

    with sqlite3.connect(f"file:{Path(audit_logger.DB_PATH).resolve()}?mode=ro", uri=True) as conn:
        rows = conn.execute('SELECT details FROM audit_events WHERE workflow_id=? AND event=?',
                            (c['workflow_id'], 'TEST_SCOPE_APPROVED')).fetchall()
    receipt_val = {'scope_hash': state['approved_scope_hash'], 'scope': state['scope']}
    if not any(json.loads(row[0]) == receipt_val for row in rows):
        raise PermissionError('Missing exact TEST_SCOPE_APPROVED audit receipt')

    baseline = load_baseline(evidence / 'originals', state['baseline_hash'])

    spec = json.loads((evidence / 'generation-response.json').read_text())
    from governance.feature_registry import get_feature_adapter
    adapter = get_feature_adapter(c.get('obligation_contract') or c)
    test_path = adapter.test_path
    contract = c.get('obligation_contract')
    if contract is None:
        if adapter.feature_id == 'expiration':
            from governance.semantic_plan import custom_expiration_contract
            contract = custom_expiration_contract()
        elif adapter.feature_id == 'health-check':
            from governance.semantic_plan import health_check_contract
            contract = health_check_contract()
    adapter.validate_spec(spec, contract)
    rendered = adapter.render(spec, contract)
    if rendered != (root / test_path).read_bytes():
        raise PermissionError('Generated test profile changed')
    if sha(rendered) != state['test_hash']:
        raise PermissionError('Generated test hash changed')

    transactions = state.get('transactions', [])
    if not transactions:
        raise PermissionError('Parallel verification requires transactions')

    seen_paths = set()
    all_changes = {}
    for tx in transactions:
        tx_id = tx['transaction_id']
        j_path = root / '.agent-transactions' / f'{tx_id}.json'
        if not j_path.exists():
            raise PermissionError(f'Transaction journal missing for {tx_id}')
        journal = json.loads(j_path.read_text())
        if journal.get('status') != 'validated' or journal.get('workflow_id') != c['workflow_id']:
            raise PermissionError(f'Transaction {tx_id} not validated for this workflow')
        if {e['path'] for e in journal['files']} != set(tx['paths']):
            raise PermissionError(f'Transaction {tx_id} paths mismatch')
        for e in journal['files']:
            path = e['path']
            if path in seen_paths:
                raise PermissionError(f'Overlapping file scope in parallel transactions: {path}')
            seen_paths.add(path)
            content = base64.b64decode(e['expected_content'], validate=True)
            orig = base64.b64decode(e['original_content'], validate=True)
            if sha(content) != e['expected_current_hash'] or baseline[path] != orig:
                raise PermissionError(f'Source snapshot/hash mismatch for {path}')
            if sha(content) != tx['expected_hashes'][path]:
                raise PermissionError(f'Transaction expected hash mismatch for {path}')
            if (root / path).read_bytes() != content:
                raise PermissionError(f'Source disk content changed for {path}')
            all_changes[path] = content

    assert_integrity(root, baseline, {**all_changes, test_path: rendered})

    records = {}; artifacts = {}
    for phase in ('baseline', 'positive'):
        records[phase], hashes = maven_record(evidence, phase)
        artifacts.update(hashes)
    if records['baseline'] != state['baseline_validation'] or records['positive'] != state['validation']:
        raise PermissionError('Maven records changed')
    if records['baseline']['exit_code'] != 0 or records['positive']['exit_code'] != 0:
        raise PermissionError('Maven validation failed')
    adapter.validate_results(records['positive'], records['baseline']['cases'])

    creation = json.loads((evidence / 'integrated-test-journal.json').read_text())
    if (creation.get('status') != 'validated'
            or creation.get('test_path') != test_path
            or creation.get('test_hash') != state['test_hash']
            or creation.get('scope_hash') != state['approved_scope_hash']):
        raise PermissionError('Additive test journal mismatch')

    for p in (evidence / 'integrated-test-journal.json', evidence / 'generation-prompt.txt', evidence / 'generation-response.json'):
        if p.exists():
            artifacts[str(p)] = sha(p.read_bytes())
    for tx in transactions:
        tx_id = tx['transaction_id'] if isinstance(tx, dict) else tx
        p = root / '.agent-transactions' / f"{tx_id}.json"
        artifacts[str(p)] = sha(p.read_bytes())

    link = state.get('engineering_test_link')
    if not link:
        raise PermissionError('Missing test linkage')

    with sqlite3.connect(f"file:{Path(audit_logger.DB_PATH).resolve()}?mode=ro", uri=True) as conn:
        raw_events = [{'id': i, 'event': e, 'details': json.loads(d)} for i, e, d in conn.execute(
            'SELECT id, event, details FROM audit_events WHERE workflow_id=? ORDER BY id', (c['workflow_id'],))]
    bound_events = [e for e in raw_events if e['event'] == 'ENGINEERING_TESTS_BOUND']
    if not bound_events:
        raise PermissionError('Missing ENGINEERING_TESTS_BOUND audit event')
    cutoff = bound_events[-1]['id']
    audit_events = [e for e in raw_events if e['id'] <= cutoff]

    actual_diff = ''.join(''.join(difflib.unified_diff(
        baseline[p].decode().splitlines(True), data.decode().splitlines(True), fromfile=p, tofile=p))
        for p, data in all_changes.items())

    tx_ids = [tx['transaction_id'] if isinstance(tx, dict) else tx for tx in transactions]
    return {
        'profile': f'parallel-{adapter.feature_id}-v1',
        'workflow_id': c['workflow_id'],
        'workspace': str(root.resolve()),
        'requirements_hash': c.get('requirements_hash', ''),
        'requirement_version': c.get('requirement_version'),
        'approved_design_hash': digest(c.get('design_artifact', {})),
        'approved_contract_hash': digest(c.get('obligation_contract', {})),
        'approved_implementation_plan_hash': digest(c['plan']),
        'implementation_hash': digest({p: sha(v) for p, v in all_changes.items()}),
        'generated_test_hash': state['test_hash'],
        'test_path': test_path,
        'source_transaction': tx_ids,
        'transactions': tx_ids,
        'records': records,
        'artifacts': artifacts,
        'requirements': c.get('structured_requirements'),
        'clarification_answers': c.get('clarification_answers'),
        'approved_design': c.get('design_artifact'),
        'approved_contract': c.get('obligation_contract'),
        'approved_plan': c['plan'],
        'actual_diff': actual_diff,
        'audit_events': audit_events,
    }


verify = verify_integrated
