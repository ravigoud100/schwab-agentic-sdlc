"""Strict requirements and clarification contracts; assumptions never resolve questions."""
import hashlib
import json
import re


class RequirementsError(ValueError):
    pass


FIELDS = {'normalized_problem', 'functional_requirements', 'non_functional_requirements',
          'acceptance_criteria', 'ambiguities', 'risks', 'classification'}


def validate_requirements(payload):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise RequirementsError('Requirements must contain exactly the required schema fields')
    if not isinstance(payload['normalized_problem'], str) or not payload['normalized_problem'].strip():
        raise RequirementsError('normalized_problem must be non-empty text')
    for field in ('functional_requirements', 'non_functional_requirements', 'acceptance_criteria', 'risks'):
        values = payload[field]
        if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
            raise RequirementsError(f'{field} must be a list of non-empty strings')
    if not payload['functional_requirements'] or not payload['acceptance_criteria']:
        raise RequirementsError('Functional requirements and acceptance criteria cannot be empty')
    if not isinstance(payload['classification'], str) or payload['classification'] not in {'greenfield','brownfield','unknown'}:
        raise RequirementsError('Unsupported requirement classification')
    if not isinstance(payload['ambiguities'], list):
        raise RequirementsError('ambiguities must be an array')
    identifiers = set()
    for ambiguity in payload['ambiguities']:
        if not isinstance(ambiguity, dict) or set(ambiguity) != {'id','question','options','default_assumption'}:
            raise RequirementsError('Each ambiguity requires id, question, options, default_assumption')
        identifier = ambiguity['id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',identifier) or identifier in identifiers:
            raise RequirementsError('Ambiguity IDs must be unique identifiers')
        identifiers.add(identifier)
        if not isinstance(ambiguity['question'], str) or not ambiguity['question'].strip():
            raise RequirementsError('Clarification questions cannot be empty')
        options = ambiguity['options']
        if not isinstance(options, list) or not all(isinstance(value,str) and value.strip() for value in options) or len(set(options)) != len(options):
            raise RequirementsError('Clarification options must be unique non-empty strings')
        assumption = ambiguity['default_assumption']
        if assumption is not None and (not isinstance(assumption,str) or not assumption.strip()):
            raise RequirementsError('default_assumption must be text or null')
        if assumption is not None and options and assumption not in options:
            raise RequirementsError('Suggested assumption must be one of the options')
    return payload


def parse_requirements(text):
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as error:
        raise RequirementsError('Requirements agent must return valid JSON without Markdown fences') from error
    return validate_requirements(payload)


def validate_answers(requirements, answers):
    validate_requirements(requirements)
    if not isinstance(answers, dict) or set(answers) != {item['id'] for item in requirements['ambiguities']}:
        raise RequirementsError('Answers must resolve every ambiguity ID exactly once; unknown IDs are not accepted')
    for ambiguity in requirements['ambiguities']:
        value = answers[ambiguity['id']]
        if not isinstance(value,str) or not value.strip():
            raise RequirementsError(f"Answer {ambiguity['id']} must be non-empty text")
        if ambiguity['options'] and value not in ambiguity['options']:
            raise RequirementsError(f"Answer {ambiguity['id']} must match one of the offered options")
    return answers


def requirements_hash(requirements, answers):
    validate_requirements(requirements)
    return hashlib.sha256(json.dumps({'requirements':requirements,'clarification_answers':answers},sort_keys=True).encode()).hexdigest()


def assert_resolved(state):
    # Low-level legacy nodes/tests can still run against older checkpoint shapes.
    # START always runs strict requirements validation for new workflow executions.
    if not state.get('requirements_hash') and 'structured_requirements' not in state:
        return
    requirements = state.get('structured_requirements', {})
    validate_requirements(requirements)
    validate_answers(requirements,state.get('clarification_answers',{}))
    if not state.get('requirements_valid') or not state.get('clarification_complete'):
        raise PermissionError('Requirements have not passed clarification and validation')
    if state.get('requirements_source_hash') != hashlib.sha256(state['requirement'].encode()).hexdigest():
        raise PermissionError('Requirement text changed without reanalysis')
    if state['requirements_hash'] != requirements_hash(requirements,state.get('clarification_answers',{})):
        raise PermissionError('Requirements changed without revalidation')


def assert_plan_current(state, *, approved=False):
    assert_resolved(state)
    from governance.design import assert_design_current
    assert_design_current(state,approved=True)
    if state.get('design_required') and state.get('plan_design_hash') != state.get('design_hash'):
        raise PermissionError('Plan belongs to a different design version')
    if state.get('semantic_required') and not state.get('obligation_contract'):
        raise PermissionError('Required obligation contract is missing')
    if state.get('obligation_contract'):
        from governance.semantic_plan import assert_contract_approved, validate_coverage, digest
        from governance.task_orchestrator import tasks_from_plan
        assert_contract_approved(state)
        if not state.get('semantic_valid') or state.get('semantic_plan_hash') != digest(state.get('plan', '')):
            raise PermissionError('Plan has not passed semantic coverage validation')
        validate_coverage(state['plan'], tasks_from_plan(state['plan'], '', strict=True), state['obligation_contract'])
    if not state.get('requirements_hash'):
        return
    if state.get('plan_requirement_version') != state.get('requirement_version'):
        raise PermissionError('Plan belongs to a different requirement version')
    if state.get('plan_requirements_hash') != state['requirements_hash']:
        raise PermissionError('Plan is stale for the current requirement version')
    if approved and state.get('approved_requirements_hash') != state['requirements_hash']:
        raise PermissionError('Current requirements have not received plan approval')


def effective_requirement(state):
    assert_resolved(state)
    if not state.get('structured_requirements'):
        return state['requirement']
    return (state['requirement'] + '\nHuman-approved answers resolve ambiguities; unselected options are not runtime requirements.\nValidated requirements and human-approved clarifications:\n' +
            json.dumps({'requirements':state['structured_requirements'],
                        'clarification_answers':state['clarification_answers']},sort_keys=True))
