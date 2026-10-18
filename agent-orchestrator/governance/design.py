"""Architecture artifact contract and deterministic stage gates."""
from pathlib import Path
from governance.requirements import assert_resolved
from governance.semantic_plan import assert_contract_approved, digest
from governance.source_policy import authorized_java_target
from governance.file_transaction import ensure_workspace_ready


class DesignError(ValueError):
    pass


SCHEMA = {
    'api_contracts':[{'id':'api-create','method':'POST','path':'/api/urls',
                     'request_fields':[{'name':'expiresAt','type':'UTC_timestamp','required':False}],
                     'validation_rules':[{'field':'expiresAt','predicate':'strictly_after_now','otherwise':'rejected'}],
                     'response_behavior':['Describe accepted, rejected and missing input behavior']}],
    'components':[{'id':'service','path':'<existing production Java path>','responsibility':'<specific responsibility>'},
                  {'id':'tests','path':'<existing Java test path>','responsibility':'<approved test cases>'}],
    'interactions':[{'from':'<component id>','to':'<other component id>','operation':'<interaction>'}],
    'data_model_changes':[{'component_id':'<component id>','field':'<field>','change':'<change or retained semantics>'}],
    'compatibility_risks':[{'id':'risk-1','description':'<risk>','mitigation':'<mitigation>'}],
    'architectural_decisions':[{'id':'decision-1','decision':'<decision>','rationale':'<rationale>'}],
    'coverage':[{'obligation_id':'<approved id>','kind':'<approved kind>','spec':{'<copy>':'<approved spec>'},
                 'component_ids':['<component id>'],'decision_ids':['<decision id>']}],
}


def entry_gate(state):
    assert_resolved(state)
    assert_contract_approved(state)
    root=Path(state['workspace']).resolve(strict=True)
    if not root.is_dir():raise DesignError('Workspace must be an initialized directory')
    if not (root/'.git').exists():raise DesignError('Workspace must be initialized as a Git worktree')
    ensure_workspace_ready(root)
    if not state.get('codebase_analysis'):raise DesignError('Codebase analysis is required')
    return root


def _text(value):
    return isinstance(value,str) and bool(value.strip())


def validate_design(artifact, state):
    root=entry_gate(state)
    if not isinstance(artifact,dict) or set(artifact)!=set(SCHEMA):
        raise DesignError('Design must contain exactly the architecture schema fields')
    for key in SCHEMA:
        if not isinstance(artifact[key],list) or (key!='data_model_changes' and not artifact[key]):
            raise DesignError(f'{key} must be a nonempty array (data_model_changes may be empty)')
    ids={}
    for section,fields in [('components',{'id','path','responsibility'}),
                           ('architectural_decisions',{'id','decision','rationale'}),
                           ('compatibility_risks',{'id','description','mitigation'})]:
        seen=set()
        for item in artifact[section]:
            if not isinstance(item,dict) or set(item)!=fields or not all(_text(v) for v in item.values()):
                raise DesignError(f'Invalid {section} record')
            if item['id'] in seen:raise DesignError(f'Duplicate {section} ID')
            seen.add(item['id'])
            if section=='components':authorized_java_target(item['path'],root,[item['path']])
        ids[section]=seen
    api_ids=set()
    for api in artifact['api_contracts']:
        if (not isinstance(api,dict) or set(api)!={'id','method','path','request_fields','validation_rules','response_behavior'}
            or not _text(api['id']) or api['id'] in api_ids
            or api['method'] not in ('GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS')
            or not _text(api['path']) or not api['path'].startswith('/')
            or not isinstance(api['request_fields'],list) or not isinstance(api['validation_rules'],list)
            or not isinstance(api['response_behavior'],list) or not api['response_behavior']
            or not all(_text(v) for v in api['response_behavior'])):
            raise DesignError('Invalid API contract')
        names=set()
        for field in api['request_fields']:
            if (not isinstance(field,dict) or set(field)!={'name','type','required'}
                or not _text(field['name']) or field['name'] in names or not _text(field['type'])
                or type(field['required']) is not bool):raise DesignError('Invalid API request field')
            names.add(field['name'])
        api_ids.add(api['id'])
        for rule in api['validation_rules']:
            if not isinstance(rule,dict) or set(rule)!={'field','predicate','otherwise'} or not all(_text(v) for v in rule.values()):
                raise DesignError('Invalid API validation rule')
    for interaction in artifact['interactions']:
        if (not isinstance(interaction,dict) or set(interaction)!={'from','to','operation'}
            or interaction['from'] not in ids['components'] or interaction['to'] not in ids['components']
            or interaction['from']==interaction['to'] or not _text(interaction['operation'])):
            raise DesignError('Invalid component interaction')
    for change in artifact['data_model_changes']:
        if (not isinstance(change,dict) or set(change)!={'component_id','field','change'}
            or change['component_id'] not in ids['components']
            or not _text(change['field']) or not _text(change['change'])):raise DesignError('Invalid data-model change')
    expected={item['id']:item for item in state['obligation_contract']['obligations']}
    for obligation in expected.values():
        spec=obligation['spec']
        if spec.get('operation')=='accept_optional_input':
            if not any(field['name']==spec['field'] and field['type']==spec['representation'] and field['required'] is False
                       for api in artifact['api_contracts'] for field in api['request_fields']):
                raise DesignError('API contract does not accept the approved optional input representation')
        if spec.get('operation')=='validate':
            rule={key:spec[key] for key in ('field','predicate','otherwise')}
            if not any(rule in api['validation_rules'] for api in artifact['api_contracts']):
                raise DesignError('API contract omits approved validation rule')
    seen=set();by_component={item['id']:item for item in artifact['components']}
    for entry in artifact['coverage']:
        if not isinstance(entry,dict) or set(entry)!={'obligation_id','kind','spec','component_ids','decision_ids'}:
            raise DesignError('Invalid structured design coverage')
        oid=entry['obligation_id']
        if not isinstance(oid,str) or oid not in expected or oid in seen:raise DesignError('Unknown/duplicate design obligation')
        obligation=expected[oid]
        if entry['kind']!=obligation['kind'] or digest(entry['spec'])!=digest(obligation['spec']):
            raise DesignError(f'Design obligation mismatch: {oid}')
        for field,valid in [('component_ids',ids['components']),('decision_ids',ids['architectural_decisions'])]:
            values=entry[field]
            if not isinstance(values,list) or not values or not all(isinstance(v,str) and v in valid for v in values):
                raise DesignError(f'Unresolved design reference: {oid}')
        if entry['kind']=='test' and not all(by_component[c]['path'].startswith('url-shortener/src/test/java/') for c in entry['component_ids']):
            test_paths=sorted(str(p.relative_to(root)) for p in (root/'url-shortener/src/test/java').rglob('*.java'))
            raise DesignError(f"Test obligation {oid} references production components {entry['component_ids']}. "
                              f"Declare a test component using one of {test_paths}, then map test obligations only to its ID.")
        seen.add(oid)
    if seen!=set(expected):raise DesignError(f'Missing design obligations: {sorted(set(expected)-seen)}')
    return artifact


def design_binding(state):
    payload={'artifact':state['design_artifact'],'requirements_hash':state['requirements_hash'],
                   'requirement_version':state['requirement_version'],
                   'contract_hash':state['approved_contract_hash']}
    # Old checkpoints predate explicit design versioning.
    if 'design_version' in state:payload['design_version']=state['design_version']
    return digest(payload)


def assert_design_current(state, approved=False):
    if not state.get('design_required'):return
    validate_design(state.get('design_artifact'),state)
    if not state.get('design_valid') or state.get('design_hash')!=design_binding(state):
        raise PermissionError('Design has not passed its exit gate for current requirements')
    if approved and state.get('approved_design_hash')!=state['design_hash']:
        raise PermissionError('Current design requires human approval')
