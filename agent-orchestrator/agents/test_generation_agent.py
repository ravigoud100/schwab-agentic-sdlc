"""Constrained expiration specification generation plus a trusted JUnit renderer.

Deliberately bounded to the reviewed custom-expiration profile. No arbitrary Java,
annotations, assertion bodies, paths, build settings or test exclusions from models.
"""
import json
from governance.semantic_plan import custom_expiration_contract,digest
from governance.feature_registry import get_feature_adapter, EXPIRATION_CASES, HEALTH_CASES

CASES={o['id']:o['spec'] for o in custom_expiration_contract()['obligations'] if o['kind']=='test'}


def generate_spec(model,context,scope):
    contract = context.get('obligation_contract')
    adapter = get_feature_adapter(contract)
    return adapter.generate_spec(model, context, scope)


def validate_spec(payload,contract):
    # Fail closed for unknown profiles rather than silently reusing expiration tests.
    try:
        adapter = get_feature_adapter(contract)
    except PermissionError as e:
        raise ValueError('No vetted test renderer for this contract') from e
    adapter.validate_spec(payload, contract)


def render(payload,contract):
    validate_spec(payload,contract)
    adapter = get_feature_adapter(contract)
    return adapter.render(payload, contract)
