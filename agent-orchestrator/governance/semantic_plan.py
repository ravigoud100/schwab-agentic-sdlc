"""Deterministic coverage of an explicitly reviewed obligation contract.

Checks declared design/test specifications, not source correctness. No keyword or
model-judge inference is used. Contract authors remain responsible for completeness.
"""
import hashlib
import json


class SemanticPlanError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_contract(contract, answers):
    if not isinstance(contract, dict) or set(contract) != {'decision_id', 'selected_answer', 'obligations'}:
        raise SemanticPlanError('Contract requires decision_id, selected_answer and obligations')
    if (not isinstance(contract['decision_id'], str) or not contract['decision_id']
        or not isinstance(contract['selected_answer'], str) or not contract['selected_answer']
        or contract['selected_answer'] != answers.get(contract['decision_id'])):
        raise SemanticPlanError('Contract does not match the approved clarification')
    obligations = contract['obligations']
    if not isinstance(obligations, list) or not obligations:
        raise SemanticPlanError('Contract obligations cannot be empty')
    ids = set()
    for item in obligations:
        if (not isinstance(item, dict) or set(item) != {'id', 'kind', 'spec'}
            or not isinstance(item['id'], str) or not item['id'].strip() or item['id'] in ids
            or item['kind'] not in ('implementation', 'test')
            or not isinstance(item['spec'], dict) or not item['spec']):
            raise SemanticPlanError('Invalid or duplicate obligation')
        ids.add(item['id'])
    return contract


def binding(state):
    return digest({'contract': state['obligation_contract'],
                   'requirements_hash': state['requirements_hash'],
                   'requirement_version': state['requirement_version']})


def assert_contract_approved(state):
    validate_contract(state['obligation_contract'], state.get('clarification_answers', {}))
    if state.get('approved_contract_hash') != binding(state):
        raise PermissionError('Obligation contract needs human approval for these requirements')


def validate_coverage(plan, tasks, contract):
    payload = json.loads(plan)
    coverage = payload.get('coverage') if isinstance(payload, dict) else None
    if not isinstance(coverage, list):
        raise SemanticPlanError('Plan requires a structured coverage array')
    expected = {item['id']: item for item in contract['obligations']}
    by_task = {task['task_id']: task for task in tasks}
    seen = set()
    for entry in coverage:
        if not isinstance(entry, dict) or set(entry) != {'obligation_id', 'kind', 'spec', 'task_id', 'files'}:
            raise SemanticPlanError('Coverage requires obligation_id, kind, spec, task_id, files')
        oid = entry['obligation_id']
        if not isinstance(oid, str) or oid not in expected or oid in seen:
            raise SemanticPlanError('Unknown or duplicate coverage obligation')
        obligation = expected[oid]
        if entry['kind'] != obligation['kind'] or digest(entry['spec']) != digest(obligation['spec']):
            raise SemanticPlanError(f'Behavior/test specification mismatch: {oid}')
        task = by_task.get(entry['task_id'])
        files = entry['files']
        if (not task or task.get('execution_type', 'implementation') != 'implementation'
            or not isinstance(files, list) or not files or not all(isinstance(p, str) for p in files)
            or not set(files).issubset(task['allowed_files'])):
            raise SemanticPlanError(f'Coverage must map to authorized implementation files: {oid}')
        if entry['kind'] == 'test' and not all(p.startswith('url-shortener/src/test/java/') for p in files):
            raise SemanticPlanError(f'Test obligation must map to existing Java test files: {oid}')
        seen.add(oid)
    if seen != set(expected):
        raise SemanticPlanError(f'Missing obligations: {sorted(set(expected) - seen)}')
    return True


def custom_expiration_contract():
    """Reviewed demo profile; never inferred from arbitrary prompt keywords."""
    obligations = [
        ('caller-expiration', 'implementation', {'operation':'accept_optional_input','field':'expiresAt','source':'caller','representation':'UTC_timestamp'}),
        ('future-only', 'implementation', {'operation':'validate','field':'expiresAt','predicate':'strictly_after_now','otherwise':'rejected'}),
        ('utc-consistency', 'implementation', {'operation':'compare_and_store','field':'expiresAt','time_basis':'UTC'}),
        ('missing-compatibility', 'implementation', {'operation':'preserve','when':'expiresAt_missing','behavior':'no_expiration_existing_links_and_redirects'}),
    ]
    for case, value, expected in [('future','now_plus_1_hour','accepted'), ('past','now_minus_1_hour','rejected'),
                                  ('boundary','now','rejected'), ('missing',None,'no_expiration')]:
        obligations.append((f'test-{case}','test',{'input_expiresAt':value,'clock':'fixed_UTC','expected':expected}))
    return {'decision_id':'A1','selected_answer':'Custom expiration timestamp',
            'obligations':[{'id':oid,'kind':kind,'spec':spec} for oid,kind,spec in obligations]}


def health_check_contract():
    """Reviewed health-check profile for status monitoring."""
    obligations = [
        ('health-service-status', 'implementation', {'operation': 'service_health', 'method': 'getHealthStatus', 'returns': 'status_map'}),
        ('health-endpoint-route', 'implementation', {'operation': 'controller_endpoint', 'path': '/health', 'method': 'GET'}),
        ('test-health-status', 'test', {'component': 'UrlService', 'method': 'getHealthStatus', 'expected_status': 'UP'}),
        ('test-health-endpoint', 'test', {'component': 'UrlController', 'path': '/health', 'expected_status': 200, 'expected_body_status': 'UP'}),
    ]
    return {
        'decision_id': 'H1',
        'selected_answer': 'HTTP Health Check Endpoint',
        'obligations': [{'id': oid, 'kind': kind, 'spec': spec} for oid, kind, spec in obligations]
    }
