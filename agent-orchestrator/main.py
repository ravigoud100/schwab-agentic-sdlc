import hashlib
import json
import uuid
from pathlib import Path
from typing import TypedDict

from governance.requirements import (
    RequirementsError, parse_requirements, validate_answers, requirements_hash,
    assert_resolved, assert_plan_current, effective_requirement,
)
from governance.semantic_plan import (
    validate_contract, binding as contract_binding, assert_contract_approved,
    validate_coverage, digest as semantic_digest,
)
from governance.design import (
    SCHEMA as DESIGN_SCHEMA, entry_gate as design_entry_gate, validate_design,
    design_binding, assert_design_current,
)
from governance.file_transaction import ensure_workspace_ready
from governance.failure_context import collect_failure
from governance.source_policy import authorized_java_target
from governance.file_transaction import finish_transaction, RecoveryRequired
from governance.workspace_manager import initialize_workspace
from governance.audit_logger import log_event, DB_PATH as AUDIT_DB_PATH
from governance import audit_logger
from agents.implementation_agent import implement_task
from agents.repair_agent import parse_repair_proposal, propose_repair
from agents.validation_agent import validate_implementation
from governance.rollback_manager import (
    create_checkpoint,
    rollback,
    file_hash,
)
from governance.repair_executor import apply_approved_repair
from governance.task_orchestrator import (
    TaskPlanError,
    task_definition_hash,
    finish_tasks,
    schedule_tasks,
    start_tasks,
    tasks_from_plan,
    validate_task_plan,
    is_parallel_eligible,
)

from dotenv import load_dotenv
from llm.provider import get_model, ModelUnavailableError

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.codebase_agent import analyze_codebase

load_dotenv()

IMPLEMENTATION_RELATIVE_PATH = (
    "url-shortener/src/main/java/url_shortener/ShortUrl.java"
)
MAX_SEMANTIC_RETRIES = 2
MAX_DESIGN_RETRIES = 2

model = get_model()



# Information shared between agents
class EngineeringState(TypedDict):
    engineering_accepted: bool
    production_release_authorized: bool
    final_review_decision: str
    final_review_bundle_hash: str
    final_review_artifact_hashes: dict
    final_human_review_required: bool
    final_review_error: str
    integrated_test_replay_state: dict
    integrated_correction_state: dict
    implementation_response_format: str
    integrated_sdlc: bool
    integration_evidence: str
    integration_cache: str
    integrated_result: dict
    parallel_evidence: str
    parallel_result: dict
    policy_approvals: list
    release_policy_approvals: list
    plan_lineage_required: bool
    plan_authorization: dict
    plan_authorization_audit_db: str
    design_required: bool
    design_artifact: dict
    design_valid: bool
    design_hash: str
    approved_design_hash: str
    design_attempts: int
    design_errors: list
    design_history: list
    plan_design_hash: str
    design_version: int
    design_versions: list
    design_feedback: str
    design_review_errors: list
    design_review_history: list
    design_revision_base_artifact: dict
    obligation_contract: dict
    approved_contract_hash: str
    semantic_attempts: int
    semantic_errors: list
    semantic_history: list
    semantic_valid: bool
    semantic_plan_hash: str
    semantic_required: bool
    structured_requirements: dict
    requirements_valid: bool
    requirements_raw_response: str
    requirements_error: str
    requirements_source_hash: str
    plan_requirement_version: int
    requirements_hash: str
    requirement_version: int
    requirement_history: list
    plan_requirements_hash: str
    approved_requirements_hash: str
    clarification_answers: dict
    clarification_complete: bool
    clarification_errors: list
    clarification_round: int
    clarification_history: list
    change_request: str
    requirement_change_errors: list
    requirement_change_ready: bool
    requirement: str
    analysis: str
    codebase_analysis: str
    plan: str
    approved_task_hash: str
    approval: str
    status: str
    implementation_file: str
    previous_implementation_proposal: dict
    previous_validation_result: dict
    validation_result: dict
    repair_proposal: str
    repair_approval: str
    retry_count: int
    recovery_enabled: bool
    implementation_error_type: str
    implementation_attempt_details: dict
    implementation_proposal: dict
    failure_context: dict
    attempt_history: list
    recovery_candidate_tasks: list
    recovery_material_change: bool
    recovery_correction_strategy: str
    recovery_change_summary: str
    recovery_approved: bool
    recovery_ready: bool
    recovery_stop_reason: str
    implementation_error: str
    implementation_transaction: dict
    rollback_allowed_files: list
    rollback_checkpoint: dict
    source_files_changed: int
    verification_source_hashes: dict
    validation_only: bool
    implementation_changed: bool
    expected_current_hash: str
    workflow_id: str
    workspace: str
    repair_change: dict
    repair_checkpoint: dict
    repair_expected_current_hash: str
    repair_applied: bool
    repair_rolled_back: bool
    repair_rollback_blocked: bool
    plan_version: int
    revised_plan: str
    plan_change_summary: str
    replan_context: dict
    replan_history: list
    replan_approval: str
    replan_meaningful_change: bool
    replan_attempted: bool
    revised_tasks: list
    tasks: list
    active_task_ids: list
    task_plan_valid: bool
    task_parallel_batches: list
    auto_orchestration: bool
    full_sdlc: bool
    legacy: bool


# Agent 1: Analyze requirements
def requirements_agent(state: EngineeringState):
    print("\n[1] REQUIREMENTS AGENT")

    version = state.get('requirement_version', 1) or 1
    prompt = f"""You are a requirements engineer. Normalize the user's requirement.
Return ONLY a JSON object with exactly these fields:
{{"normalized_problem":"...", "functional_requirements":["..."],
"non_functional_requirements":[], "acceptance_criteria":["..."],
"ambiguities":[{{"id":"A1","question":"...","options":["choice one","choice two"],
"default_assumption":null}}], "risks":[], "classification":"brownfield"}}
classification must be greenfield, brownfield, or unknown. Use an empty ambiguities
array only when no decisions need clarification. Options can be empty for a free-text
question. Suggested defaults are UNCONFIRMED and never resolve a question.
Do not invent business rules, thresholds, or acceptance decisions. Identify missing
behavior as explicit clarification questions. Functional requirements and acceptance
criteria must be non-empty arrays describing the requested behavior, even when a
configuration decision remains unresolved. No Markdown fences or extra fields.
User requirement:
{state['requirement']}
"""
    raw = ''
    try:
        raw = _response_text(model.invoke(prompt, stage='requirements'))
        requirements = parse_requirements(raw)
    except ModelUnavailableError as error:
        log_event(workflow_id=state['workflow_id'], agent='requirements_agent', event='MODEL_UNAVAILABLE',
                  details={'version':version,'error_type':type(error).__name__,'error':str(error),'stage':'requirements'})
        return {'requirements_valid':False,'requirements_error':str(error),'requirements_raw_response':'',
                'structured_requirements':{},'clarification_complete':False,
                'status':'STOPPED_MODEL_UNAVAILABLE'}
    except Exception as error:
        log_event(workflow_id=state['workflow_id'], agent='requirements_agent', event='REQUIREMENTS_INVALID',
                  details={'version':version,'error_type':type(error).__name__,'error':str(error)})
        return {'requirements_valid':False,'requirements_error':str(error),'requirements_raw_response':raw,
                'structured_requirements':{},'clarification_complete':False,
                'status':'STOPPED_INVALID_REQUIREMENTS'}
    complete = not requirements['ambiguities']
    digest = requirements_hash(requirements,{})
    log_event(workflow_id=state['workflow_id'], agent='requirements_agent', event='REQUIREMENTS_VALIDATED',
              details={'version':version,'requirements_hash':digest,'ambiguity_ids':[item['id'] for item in requirements['ambiguities']]})
    return {'structured_requirements':requirements,'requirements_valid':True,'requirements_error':'',
            'requirements_raw_response':raw,'requirements_hash':digest,'requirement_version':version,
            'requirements_source_hash':file_hash(state['requirement'].encode()),
            'clarification_answers':{},'clarification_complete':complete,'clarification_errors':[],
            'clarification_round':0,'analysis':json.dumps({'requirements':requirements,'clarification_answers':{}},sort_keys=True),
            'status':'REQUIREMENTS_ANALYZED' if complete else 'CLARIFICATION_REQUIRED'}


def requirements_router(state):
    if state.get('status') == 'STOPPED_MODEL_UNAVAILABLE':
        return 'stop'
    if not state.get('requirements_valid'):
        return 'stop'
    return 'clarify' if not state.get('clarification_complete') else 'codebase'


def clarification_node(state: EngineeringState):
    response = interrupt({'type':'REQUIREMENT_CLARIFICATION','requirement_version':state['requirement_version'],
                          'requirements':state['structured_requirements'],
                          'questions':state['structured_requirements']['ambiguities'],
                          'errors':state.get('clarification_errors',[]),
                          'message':'Answer every question and explicitly approve the answers. Suggested defaults are not accepted automatically.'})
    if isinstance(response,dict) and response.get('decision')=='reject':
        log_event(workflow_id=state['workflow_id'],agent='human_clarification',event='CLARIFICATION_REJECTED',
                  details={'requirement_version':state['requirement_version']})
        return {'clarification_complete':False,'status':'STOPPED_CLARIFICATION_REJECTED'}
    try:
        if not isinstance(response,dict) or set(response)!={'decision','answers'} or response['decision']!='approve':
            raise RequirementsError('Clarification requires explicit approval and a complete answers object')
        answers=validate_answers(state['structured_requirements'],response['answers'])
    except RequirementsError as error:
        log_event(workflow_id=state['workflow_id'],agent='human_clarification',event='CLARIFICATION_INVALID',
                  details={'requirement_version':state['requirement_version'],'error':str(error),
                           'round':state.get('clarification_round',0)+1})
        return {'clarification_complete':False,'clarification_errors':[str(error)],
                'clarification_round':state.get('clarification_round',0)+1,'status':'CLARIFICATION_REQUIRED'}
    digest=requirements_hash(state['structured_requirements'],answers)
    decision={'requirement_version':state['requirement_version'],'answers':answers,'requirements_hash':digest}
    log_event(workflow_id=state['workflow_id'],agent='human_clarification',event='CLARIFICATION_APPROVED',details=decision)
    return {'clarification_answers':answers,'clarification_complete':True,'clarification_errors':[],
            'clarification_history':state.get('clarification_history',[])+[decision],
            'requirements_hash':digest,
            'analysis':json.dumps({'requirements':state['structured_requirements'],'clarification_answers':answers},sort_keys=True),
            'plan':'','tasks':[],'active_task_ids':[],'task_plan_valid':False,
            'approved_task_hash':'','approval':'','approved_requirements_hash':'','plan_requirements_hash':'',
            'status':'REQUIREMENTS_CLARIFIED'}


def clarification_router(state):
    if state.get('status')=='STOPPED_CLARIFICATION_REJECTED': return 'stop'
    return 'codebase' if state.get('clarification_complete') else 'clarify'


def contract_review_node(state: EngineeringState):
    contract = state.get('obligation_contract')
    if not contract:
        if state.get('semantic_required') or state.get('design_required'):
            log_event(workflow_id=state['workflow_id'],agent='requirement_contract_review',event='STAGE_GATE_REJECTED',
                      details={'stage':'design' if state.get('design_required') else 'planning',
                               'gate':'entry','error':'Explicit approved obligation contract required'})
            return {'status':'STOPPED_CONTRACT_REQUIRED','approved_contract_hash':''}
        return {}  # Compatibility for workflows without a supplied machine contract.
    assert_resolved(state)
    try:
        validate_contract(contract, state.get('clarification_answers', {}))
    except (ValueError, TypeError) as error:
        log_event(workflow_id=state['workflow_id'], agent='requirement_contract_review',
                  event='REQUIREMENT_CONTRACT_INVALID', details={'error':str(error)})
        return {'status':'STOPPED_CONTRACT_INVALID','approved_contract_hash':'','semantic_required':True}
    decision = interrupt({'type':'REQUIREMENT_CONTRACT_APPROVAL',
                          'contract':contract, 'requirement_version':state['requirement_version'],
                          'requirements_hash':state['requirements_hash'],
                          'message':'Review explicit obligations before planning; this does not approve edits.'})
    approved = decision == 'approve'
    log_event(workflow_id=state['workflow_id'], agent='requirement_contract_review',
              event='REQUIREMENT_CONTRACT_APPROVED' if approved else 'REQUIREMENT_CONTRACT_REJECTED',
              details={'contract':contract,'contract_hash':contract_binding(state),
                       'requirements_hash':state['requirements_hash'],
                       'requirement_version':state['requirement_version']})
    return {'approved_contract_hash':contract_binding(state) if approved else '',
            'semantic_required':True,
            'semantic_attempts':0,'semantic_errors':[], 'semantic_valid':False,
            'status':'REQUIREMENT_CONTRACT_APPROVED' if approved else 'STOPPED_CONTRACT_REJECTED'}


def design_node(state: EngineeringState):
    try:
        root=design_entry_gate(state)
    except Exception as error:
        log_event(workflow_id=state['workflow_id'],agent='design_entry_gate',event='STAGE_GATE_REJECTED',
                  details={'stage':'design','gate':'entry','error':str(error)})
        return {'status':'STOPPED_DESIGN_ENTRY_GATE','design_valid':False,'approved_design_hash':''}
    log_event(workflow_id=state['workflow_id'],agent='design_entry_gate',event='STAGE_GATE_PASSED',
              details={'stage':'design','gate':'entry','requirements_hash':state['requirements_hash'],
                       'requirement_version':state['requirement_version'],'contract_hash':state['approved_contract_hash']})
    inventory=sorted(str(p.relative_to(root)) for p in (root/'url-shortener/src').rglob('*.java'))
    prompt=f'''You are the architecture/design agent. Return ONLY one JSON architecture artifact.
Do not generate source edits, tasks, Markdown, or additional fields.
Approved requirement version: {state['requirement_version']}
Approved clarification: {json.dumps(state['clarification_answers'])}
Approved machine contract: {json.dumps(state['obligation_contract'])}
The contract and human choices supersede unresolved alternatives in earlier analysis.
Codebase analysis: {state['codebase_analysis']}
Existing Java files (no new files authorized): {json.dumps(inventory)}
Required exact artifact schema (replace placeholders; expand coverage to ALL approved obligations):
{json.dumps(DESIGN_SCHEMA)}
Use unique component and decision IDs. Every interaction references two distinct component IDs.
Include components for existing test files as well as production components.
data_model_changes.component_id must reference a declared component ID, not a class name.
Every coverage entry copies the obligation kind and spec exactly and references existing
component and architectural decision IDs. Test obligations reference Java test components.
API contracts, component responsibilities/interactions, data-model changes (empty if unnecessary),
compatibility risks with mitigations, and architectural decisions with rationales are required.
No implementation is authorized by this artifact. Preserve existing behavior.
Human design-review feedback: {state.get('design_feedback','')}
Artifact under correction: {json.dumps(state.get('design_revision_base_artifact',{}))}
Correct the review feedback without changing the approved requirement or obligation contract.
The expiration configuration decision is already approved; do not describe it as unresolved.
Previous exit-gate errors: {json.dumps(state.get('design_errors',[]))}
Previous rejected artifact (correct the precise reference errors rather than repeating it):
{state.get('design_history',[])[-1].get('raw_response','') if state.get('design_history') and state.get('design_errors') else ''}
'''
    raw=''
    try:
        raw=_response_text(model.invoke(prompt,stage='design'))
        artifact=json.loads(raw)
        validate_design(artifact,state)
        if state.get('design_feedback') and artifact==state.get('design_revision_base_artifact'):
            raise ValueError('Requested design correction returned an unchanged artifact')
    except ModelUnavailableError as error:
        log_event(workflow_id=state['workflow_id'], agent='design_node', event='MODEL_UNAVAILABLE',
                  details={'stage': 'design', 'error_type': type(error).__name__, 'error': str(error)})
        return {'design_valid': False, 'design_hash': '', 'approved_design_hash': '', 'design_artifact': {},
                'design_errors': [str(error)], 'status': 'STOPPED_MODEL_UNAVAILABLE'}
    except Exception as error:
        attempt=state.get('design_attempts',0)+1
        evidence={'attempt':attempt,'error':str(error),'raw_response':raw,
                  'requirements_hash':state['requirements_hash'],'contract_hash':state['approved_contract_hash'],
                  'requirement_version':state['requirement_version']}
        log_event(workflow_id=state['workflow_id'],agent='design_exit_gate',event='STAGE_GATE_REJECTED',
                  details={'stage':'design','gate':'exit',**evidence})
        return {'design_valid':False,'design_hash':'','approved_design_hash':'','design_artifact':{},
                'design_attempts':attempt,'design_errors':[str(error)],
                'design_history':state.get('design_history',[])+[evidence],
                'status':'STOPPED_DESIGN_INVALID' if attempt>=MAX_DESIGN_RETRIES+1 else 'DESIGN_RETRY'}
    version=state.get('design_version',0)+1
    bound=design_binding({**state,'design_artifact':artifact,'design_version':version})
    record={'version':version,'artifact':artifact,'design_hash':bound,
            'requirements_hash':state['requirements_hash'],'contract_hash':state['approved_contract_hash'],
            'requirement_version':state['requirement_version']}
    log_event(workflow_id=state['workflow_id'],agent='design_exit_gate',event='STAGE_GATE_PASSED',
              details={'stage':'design','gate':'exit','design_hash':bound,
                       'design_version':version,
                       'requirement_version':state['requirement_version'],
                       'requirements_hash':state['requirements_hash'],'contract_hash':state['approved_contract_hash']})
    return {'design_artifact':artifact,'design_valid':True,'design_hash':bound,
            'design_version':version,'design_versions':state.get('design_versions',[])+[record],
            'design_review_errors':[],
            'approved_design_hash':'','design_errors':[],'status':'AWAITING_DESIGN_APPROVAL',
            'semantic_attempts':0,'semantic_errors':[],'semantic_valid':False,'semantic_plan_hash':'',
            'plan':'','tasks':[],'approval':'','approved_task_hash':'','approved_requirements_hash':'','plan_design_hash':''}


def design_review_node(state: EngineeringState):
    assert_design_current(state)
    decision=interrupt({'type':'DESIGN_APPROVAL','artifact':state['design_artifact'],
                        'design_hash':state['design_hash'],'requirement_version':state['requirement_version'],
                        'design_version':state.get('design_version',1),
                        'allowed_decisions':['approve','reject','request_changes'],
                        'errors':state.get('design_review_errors',[]),
                        'requirements_hash':state['requirements_hash'],
                        'message':'Review architecture and design. This does not approve source edits.'})
    feedback=''
    if isinstance(decision,dict):
        response=decision
        decision=response.get('decision')
        feedback=response.get('feedback','')
        valid_keys={'decision','feedback'} if decision=='request_changes' else {'decision'}
        if set(response)!=valid_keys:decision=None
    if decision not in ('approve','reject','request_changes') or (
        decision=='request_changes' and (not isinstance(feedback,str) or not feedback.strip())):
        log_event(workflow_id=state['workflow_id'],agent='design_review',event='DESIGN_REVIEW_INVALID',
                  details={'design_hash':state['design_hash'],'error':'Explicit decision and nonempty correction feedback required'})
        return {'approved_design_hash':'','design_review_errors':['Provide approve, reject, or request_changes with nonempty feedback.'],
                'status':'DESIGN_REVIEW_INVALID'}
    review={'decision':decision,'feedback':feedback,'design_hash':state['design_hash'],
            'design_version':state.get('design_version',1),'requirements_hash':state['requirements_hash']}
    if decision=='request_changes':
        version=state.get('design_version',1)
        versions=list(state.get('design_versions',[]))
        if not any(item['design_hash']==state['design_hash'] for item in versions):
            versions.append({'version':version,'artifact':state['design_artifact'],'design_hash':state['design_hash'],
                             'requirements_hash':state['requirements_hash'],'contract_hash':state['approved_contract_hash'],
                             'requirement_version':state['requirement_version']})
        log_event(workflow_id=state['workflow_id'],agent='design_review',event='DESIGN_CHANGES_REQUESTED',
                  details={**review,'artifact':state['design_artifact'],'contract_hash':state['approved_contract_hash']})
        return {'design_version':version,'design_versions':versions,'design_feedback':feedback.strip(),
                'design_revision_base_artifact':state['design_artifact'],'design_valid':False,
                'approved_design_hash':'','design_attempts':0,'design_errors':[],'design_review_errors':[],
                'design_review_history':state.get('design_review_history',[])+[review],
                'plan':'','tasks':[],'approval':'','approved_task_hash':'','approved_requirements_hash':'',
                'plan_design_hash':'','semantic_valid':False,'semantic_plan_hash':'',
                'plan_authorization':{},'plan_authorization_audit_db':'',
                'semantic_attempts':0,'semantic_errors':[],
                'status':'DESIGN_CHANGES_REQUESTED'}
    approved=decision=='approve'
    log_event(workflow_id=state['workflow_id'],agent='design_review',
              event='DESIGN_APPROVED' if approved else 'DESIGN_REJECTED',
              details=review)
    return {'approved_design_hash':state['design_hash'] if approved else '',
            'design_review_history':state.get('design_review_history',[])+[review],
            'status':'DESIGN_APPROVED' if approved else 'STOPPED_DESIGN_REJECTED'}


def requirement_changes_node(state: EngineeringState):
    supplied=state.get('change_request','')
    if supplied:
        response={'decision':'submit','requirement':supplied}
    else:
        response=interrupt({'type':'REQUIREMENT_CHANGES','current_requirement':state['requirement'],
                            'errors':state.get('requirement_change_errors',[]),
                            'message':'Provide the revised requirement. Prior plans and approvals will be invalidated.'})
    if isinstance(response,dict) and response.get('decision')=='cancel':
        log_event(workflow_id=state['workflow_id'],agent='human_requirements_review',event='REQUIREMENT_CHANGES_CANCELLED',details={})
        return {'status':'STOPPED_REQUIREMENT_CHANGES_CANCELLED','requirement_change_ready':False}
    if (not isinstance(response,dict) or set(response)!={'decision','requirement'} or response['decision']!='submit'
        or not isinstance(response['requirement'],str) or not response['requirement'].strip()
        or response['requirement'].strip()==state['requirement'].strip()):
        return {'change_request':'','requirement_change_errors':['Provide a non-empty changed requirement or cancel.'],
                'requirement_change_ready':False,'status':'REQUIREMENT_CHANGES_REQUIRED'}
    if state.get('workspace'):
        try:
            ensure_workspace_ready(Path(state['workspace']))
        except RecoveryRequired:
            return {'status':'STOPPED_ROLLBACK_REQUIRES_REVIEW','requirement_change_ready':False}
    version=state.get('requirement_version',1)+1
    old={'version':state.get('requirement_version',1),'requirement':state['requirement'],
         'structured_requirements':state.get('structured_requirements',{}),
         'clarification_answers':state.get('clarification_answers',{}),'plan':state.get('plan',''),
         'requirements_hash':state.get('requirements_hash',''),'approved_task_hash':state.get('approved_task_hash',''),
         'obligation_contract':state.get('obligation_contract',{}),
         'approved_contract_hash':state.get('approved_contract_hash','')}
    log_event(workflow_id=state['workflow_id'],agent='human_requirements_review',event='REQUIREMENTS_CHANGED',
              details={'old_version':version-1,'new_version':version,'previous_requirements_hash':state.get('requirements_hash','')})
    log_event(workflow_id=state['workflow_id'],agent='requirements_gate',event='DOWNSTREAM_PLANS_INVALIDATED',
              details={'requirement_version':version,'previous_plan_hash':file_hash(state.get('plan','').encode()),
                       'previous_approved_task_hash':state.get('approved_task_hash','')})
    return {'requirement':response['requirement'].strip(),'requirement_version':version,
            'requirement_history':state.get('requirement_history',[])+[old],
            'structured_requirements':{},'requirements_hash':'','requirements_source_hash':'','requirements_valid':False,
            'plan_requirement_version':0,
            'clarification_answers':{},'clarification_complete':False,'clarification_errors':[],
            'analysis':'','codebase_analysis':'','plan':'','tasks':[],'active_task_ids':[],
            'task_plan_valid':False,'task_parallel_batches':[],'approval':'','approved_task_hash':'','approved_requirements_hash':'',
            'plan_requirements_hash':'','change_request':'','requirement_change_errors':[],
            'requirement_change_ready':True,'validation_result':{},'implementation_transaction':{},
            'obligation_contract':{},'approved_contract_hash':'','semantic_attempts':0,
            'design_artifact':{},'design_valid':False,'design_hash':'','approved_design_hash':'',
            'plan_design_hash':'',
            'plan_authorization':{},'plan_authorization_audit_db':'',
            'design_attempts':0,'design_errors':[],
            'design_version':0,'design_feedback':'','design_revision_base_artifact':{},'design_review_errors':[],
            'semantic_errors':[],'semantic_valid':False,'semantic_plan_hash':'',
            'rollback_checkpoint':{},'rollback_allowed_files':[],'implementation_changed':False,
            'expected_current_hash':'','implementation_file':'','source_files_changed':0,
            'retry_count':0,'failure_context':{},'recovery_candidate_tasks':[],'recovery_approved':False,
            'implementation_proposal':{},'implementation_attempt_details':{},'implementation_error':'',
            'implementation_error_type':'','verification_source_hashes':{},'validation_only':False,
            'recovery_correction_strategy':'','recovery_change_summary':'','recovery_material_change':False,
            'recovery_ready':False,'repair_change':{},'repair_checkpoint':{},'repair_applied':False,
            'repair_approval':'','repair_rolled_back':False,'repair_rollback_blocked':False,
            'revised_tasks':[],'revised_plan':'','replan_approval':'','replan_attempted':False,
            'replan_meaningful_change':False,'previous_implementation_proposal':{},'previous_validation_result':{},
            'status':'REQUIREMENTS_CHANGED'}


# Agent 2: Analyze actual Java code
def codebase_agent(state: EngineeringState):
    print("\n[2] CODEBASE ANALYSIS AGENT")

    result = analyze_codebase(effective_requirement(state), Path(state["workspace"]))

    return {
        "codebase_analysis": result,
        "status": "CODEBASE_ANALYZED"
    }


# Agent 3: Generate an implementation plan
def planning_agent(state: EngineeringState):
    assert_resolved(state)
    assert_design_current(state,approved=True)
    contract = state.get('obligation_contract')
    if contract:
        assert_contract_approved(state)
    existing_tests = sorted(str(path.relative_to(Path(state['workspace'])))
                            for path in (Path(state['workspace']) / 'url-shortener/src/test/java').rglob('*.java')) if contract else []
    coverage_schema = (', "coverage": [{"obligation_id":"<approved obligation id>",'
                       '"kind":"<approved kind>","spec":{"<copy approved spec keys>":"<and values>"},'
                       '"task_id":"<task id from tasks>","files":["<existing file in task allowed_files>"]}]') if contract else ''
    if state.get('plan_lineage_required'):
        from governance.feature_registry import get_feature_adapter
        try:
            adapter = get_feature_adapter(contract or state.get('requirement') or state)
            proposed_test_path = adapter.test_path
        except Exception:
            proposed_test_path = "url-shortener/src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java"
        coverage_schema += f', "test_stage": {{"allowed_new_test_files":["{proposed_test_path}"], "protected_existing_test_files":["<existing test paths referenced by tasks>"]}}'
    print("\n[3] PLANNING AGENT")

    try:
        response = model.invoke(
            f"""
        You are a senior Java software architect.

        Original requirement:
        {state["requirement"]}

        Validated requirement version: {state.get('requirement_version', 0)}
        Human-approved clarification answers: {json.dumps(state.get('clarification_answers', {}))}
        These answers are binding decisions. Implement the selected options only.
        Unselected alternatives and suggested defaults are not requirements or
        additional runtime features. Resolve any analysis ambiguity using the answers.
        Task descriptions MUST state the concrete behavior to deliver, including
        the selected clarification decisions and relevant acceptance criteria.
        Do not copy the generic example description or leave the approved choices
        implicit. Do not ask again about questions already answered by the human.
        Requirement analysis:
        {state["analysis"]}

        Existing codebase analysis:
        {state["codebase_analysis"]}
        Approved architecture/design: {json.dumps(state.get('design_artifact',{}))}
        Prospective plan lineage required: {state.get('plan_lineage_required',False)}
        When lineage is required, add top-level test_stage with exactly
        allowed_new_test_files (at most one explicitly proposed new Java test path)
        and protected_existing_test_files (existing test paths referenced by tasks,
        read-only). Only necessary production files may be edited. Existing tests
        must remain unchanged; additive generation has its own approval/gate.

        Human-approved machine-checkable obligation contract:
        {json.dumps(contract)}
        Existing test files available for approved coordinated edits: {json.dumps(existing_tests)}
        If a contract is supplied, add a top-level coverage array. Each entry MUST
        have exactly obligation_id, kind, spec, task_id, files. Copy each approved
        obligation's kind and structured spec exactly; map it to an implementation
        task and a nonempty subset of that task's allowed_files. Test obligations
        MUST map to existing Java test files under url-shortener/src/test/java/.
        Include those existing test files in the coordinated task allowlist.
        Cover every obligation exactly once; no unsupported obligation IDs.
        Previous semantic validation errors (fix these):
        {json.dumps(state.get('semantic_errors', []))}
        Previous rejected proposal:
        {state.get('semantic_history', [])[-1].get('proposal', '') if state.get('semantic_history') and state.get('semantic_errors') else ''}

        Return ONLY valid JSON. No Markdown fences, prose, locate tasks, or test tasks.
        Group coordinated edits into one complete implementation task with all
        affected existing Java files; do not split a coordinated feature into partial tasks. Maven validation runs separately.
        Implementation edits existing production files only. When lineage is enabled,
        the separate test_stage policy proposes one additive test; it never belongs
        in the implementation task allowlist. Use repository-relative paths supplied above.
        Each allowed_files value MUST be a non-empty array of approved existing Java paths.
        execution_type MUST be the literal string "implementation".
        Use this exact schema (replace the example description and file as appropriate):
        {{"tasks": [{{"task_id": "implement-requirement",
          "description": "<state concrete approved behavior and testing obligations>",
          "dependencies": [], "execution_type": "implementation",
          "allowed_files": ["url-shortener/src/main/java/url_shortener/ShortUrl.java"]}}]{coverage_schema}}}
        Expand coverage to ALL approved obligations. The machine contract and human
        answers supersede unresolved alternatives in the original requirement or
        stale codebase analysis. Do not offer both runtime expiration modes.
        """,
            stage="planning",
        )
    except ModelUnavailableError as error:
        log_event(workflow_id=state['workflow_id'], agent='planning_agent', event='MODEL_UNAVAILABLE',
                  details={'stage': 'planning', 'error_type': type(error).__name__, 'error': str(error)})
        return {'status': 'STOPPED_MODEL_UNAVAILABLE', 'task_plan_valid': False}

    plan = response.text
    try:
        tasks = tasks_from_plan(plan, IMPLEMENTATION_RELATIVE_PATH, strict=True)
        if contract:
            validate_coverage(plan, tasks, contract)
            for task in tasks:
                for path in task['allowed_files']:
                    authorized_java_target(path, Path(state['workspace']), task['allowed_files'])
    except (ValueError, TypeError, KeyError, OSError, PermissionError) as error:
        if not contract:
            raise
        attempt = state.get('semantic_attempts', 0) + 1
        evidence = {'attempt': attempt, 'error': str(error), 'proposal': plan,
                    'requirements_hash': state['requirements_hash'],
                    'contract_hash': state['approved_contract_hash'],
                    'requirement_version': state['requirement_version']}
        log_event(workflow_id=state['workflow_id'], agent='semantic_plan_gate',
                  event='SEMANTIC_PLAN_REJECTED', details=evidence)
        if attempt >= MAX_SEMANTIC_RETRIES + 1:
            log_event(workflow_id=state['workflow_id'], agent='semantic_plan_gate',
                      event='SEMANTIC_PLAN_EXHAUSTED',
                      details={'attempts':attempt,'requirements_hash':state['requirements_hash'],
                               'contract_hash':state['approved_contract_hash']})
        return {'semantic_valid': False, 'semantic_errors': [str(error)],
                'semantic_attempts': attempt,
                'semantic_history': state.get('semantic_history', []) + [evidence],
                'plan': '', 'tasks': [], 'approved_task_hash': '', 'approval': '',
                'approved_requirements_hash':'', 'semantic_plan_hash':'',
                'plan_requirements_hash':'', 'plan_requirement_version':0,
                'status': 'STOPPED_SEMANTIC_PLAN_INVALID' if attempt >= MAX_SEMANTIC_RETRIES + 1 else 'SEMANTIC_PLAN_RETRY'}
    if contract:
        log_event(workflow_id=state['workflow_id'], agent='semantic_plan_gate', event='SEMANTIC_PLAN_ACCEPTED',
                  details={'attempt':state.get('semantic_attempts',0)+1,
                           'contract_hash':state['approved_contract_hash'],
                           'requirements_hash':state['requirements_hash'],
                           'plan_hash':semantic_digest(plan)})
    log_event(workflow_id=state['workflow_id'],agent='planning_agent',event='PLAN_CREATED',
              details={'plan_version':state.get('plan_version',0)+1,
                       'requirement_version':state.get('requirement_version',0),
                       'requirements_hash':state.get('requirements_hash',''),
                       'plan_hash':file_hash(plan.encode()),'task_definition_hash':task_definition_hash(tasks)})
    return {
        "semantic_valid": True,
        "plan_design_hash": state.get('design_hash','') if state.get('design_required') else '',
        "semantic_errors": [],
        "semantic_plan_hash": semantic_digest(plan) if contract else '',
        "plan": plan,
        "plan_requirements_hash": state.get("requirements_hash", ""),
        "plan_requirement_version": state.get("requirement_version", 0),
        "plan_version": state.get("plan_version", 0) + 1,
        "replan_history": state.get('replan_history', []) + [
            {"version": state.get('plan_version',0)+1, "requirement_version":state.get('requirement_version',0),
             "plan": plan, "source": "initial"}
        ],
        "tasks": tasks,
        "status": "PLAN_CREATED"
    }


def _response_text(response) -> str:
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", response)
    return content if isinstance(content, str) else str(content)


def _parse_replan_response(response) -> tuple[str, str]:
    content = _response_text(response).strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("Replanning response must be valid JSON") from error

    if not isinstance(payload, dict):
        raise ValueError("Replanning response must be a JSON object")
    if set(payload) != {"revised_plan", "change_summary"}:
        raise ValueError(
            "Replanning response requires revised_plan and change_summary"
        )
    revised_plan = payload["revised_plan"]
    change_summary = payload["change_summary"]
    if not isinstance(revised_plan, str) or not revised_plan.strip():
        raise ValueError("Revised plan must be non-empty text")
    if not isinstance(change_summary, str) or not change_summary.strip():
        raise ValueError("Plan change summary must be non-empty text")
    return revised_plan.strip(), change_summary.strip()


def replanning_node(state: EngineeringState):
    print("\n[REPLANNING AGENT]")
    failure = state.get("validation_result", {})
    retry_history = state.get("replan_history", [])
    context = {
        "original_requirement": state["requirement"],
        "current_plan": state["plan"],
        "failure_details": {
            "passed": failure.get("passed", False),
            "exit_code": failure.get("exit_code"),
            "duration_seconds": failure.get("duration_seconds"),
            "output": failure.get("output", ""),
        },
        "retry_history": retry_history,
    }
    prompt = f"""
You are revising a Java 21 Spring Boot 4 engineering plan after validation failed.
Do not execute commands, change files, change Java versions, or weaken validation.
Return only valid JSON with exactly these string fields:
{{"revised_plan": "...", "change_summary": "..."}}

Context:
{json.dumps(context, indent=2)}
"""

    try:
        revised_plan, change_summary = _parse_replan_response(model.invoke(prompt, stage="planning"))
    except Exception as error:
        log_event(
            workflow_id=state["workflow_id"],
            agent="replanning_agent",
            event="REPLAN_BLOCKED",
            details={"error_type": type(error).__name__},
        )
        return {
            "replan_attempted": True,
            "replan_meaningful_change": False,
            "status": "REPLAN_BLOCKED",
        }

    meaningful = " ".join(revised_plan.split()) != " ".join(
        state["plan"].split()
    )
    revised_tasks = tasks_from_plan(
        revised_plan,
        IMPLEMENTATION_RELATIVE_PATH,
    )
    version = state.get("plan_version", 0) + 1
    history_entry = {
        "version": version,
        "plan": revised_plan,
        "change_summary": change_summary,
        "source": "replan",
    }
    log_event(
        workflow_id=state["workflow_id"],
        agent="replanning_agent",
        event="REPLAN_GENERATED" if meaningful else "REPLAN_UNCHANGED",
        details={
            "plan_version": version,
            "meaningful_change": meaningful,
            "retry_count": state.get("retry_count", 0),
        },
    )
    return {
        "revised_plan": revised_plan,
        "plan_change_summary": change_summary,
        "plan_version": version,
        "replan_context": context,
        "replan_history": retry_history + [history_entry],
        "revised_tasks": revised_tasks,
        "replan_meaningful_change": meaningful,
        "replan_attempted": True,
        "replan_approval": "",
        "status": "REPLAN_REVIEW_REQUIRED" if meaningful else "REPLAN_UNCHANGED",
    }


def replan_review_node(state: EngineeringState):
    if not state.get("replan_meaningful_change"):
        return {"replan_approval": "reject", "status": "REPLAN_UNCHANGED"}

    print("\n[REPLAN REVIEW] Human approval required")
    decision = interrupt({
        "type": "REPLAN_APPROVAL",
        "plan_version": state["plan_version"],
        "revised_plan": state["revised_plan"],
        "change_summary": state["plan_change_summary"],
        "message": "Approve or reject the revised engineering plan.",
    })
    approved = str(decision).strip().lower() == "approve"
    log_event(
        workflow_id=state["workflow_id"],
        agent="human_replan_review",
        event="REPLAN_APPROVED" if approved else "REPLAN_REJECTED",
        details={"plan_version": state["plan_version"]},
    )
    updates: dict[str, object] = {
        "replan_approval": "approve" if approved else "reject",
        "status": "REPLAN_APPROVED" if approved else "REPLAN_REJECTED",
    }
    if approved:
        try:
            scheduled, ready, parallel_batches = schedule_tasks(
                state["revised_tasks"]
            )
        except TaskPlanError as error:
            log_event(
                workflow_id=state["workflow_id"],
                agent="task_scheduler",
                event="TASK_SCHEDULING_FAILED",
                details={"error_type": type(error).__name__},
            )
            return {
                "replan_approval": "reject",
                "status": "STOPPED_INVALID_TASK_PLAN",
            }
        updates["approved_task_hash"] = _task_definition_hash(
            tasks_from_plan(state["revised_plan"], IMPLEMENTATION_RELATIVE_PATH)
        )
        updates["plan"] = state["revised_plan"]
        updates["tasks"] = scheduled
        updates["active_task_ids"] = ready[:1]
        updates["task_parallel_batches"] = parallel_batches
        updates["task_plan_valid"] = bool(ready)
    return updates


def replan_approval_router(state: EngineeringState):
    if (
        state.get("replan_approval") == "approve"
        and state.get("replan_meaningful_change")
        and state.get("task_plan_valid", True)
        and state.get("active_task_ids")
    ):
        return "approved"
    return "rejected"


def replan_stopped_node(state: EngineeringState):
    log_event(
        workflow_id=state["workflow_id"],
        agent="replanning_agent",
        event="REPLAN_STOPPED",
        details={
            "plan_version": state.get("plan_version", 0),
            "meaningful_change": state.get("replan_meaningful_change", False),
        },
    )
    return {"status": "STOPPED_REPLAN_NOT_APPLIED"}


# Retain the existing helper name for test and approval compatibility.
_task_definition_hash = task_definition_hash


def _require_approved_validation_plan(state: EngineeringState):
    assert_plan_current(state, approved=True)
    if (
        state.get("approval") != "approve"
        or not state.get("approved_task_hash")
        or state["approved_task_hash"] != _task_definition_hash(state["tasks"])
    ):
        raise PermissionError("Validation-only execution requires the approved task plan")


# Human approval checkpoint
def human_approval(state: EngineeringState):
    assert_plan_current(state)
    print("\n[4] HUMAN APPROVAL REQUIRED")

    decision = interrupt({
        "message": "Review the engineering plan",
        "requirement": state["requirement"],
        "plan": state["plan"],
        "requirement_version": state.get("requirement_version", 0),
        "clarification_answers": state.get("clarification_answers", {}),
        "allowed_decisions": ["approve", "reject", "request_changes"]
    })

    change_request = ''
    if isinstance(decision,dict):
        change_request = decision.get('requirement','')
        decision = decision.get('decision','reject')
    if not isinstance(decision,str) or decision not in {'approve','reject','request_changes'}:
        decision = 'reject'
    approved = decision == "approve"
    if decision == 'request_changes':
        log_event(workflow_id=state['workflow_id'],agent='human_approval',event='PLAN_CHANGES_REQUESTED',
                  details={'requirement_version':state.get('requirement_version',0),
                           'requirements_hash':state.get('requirements_hash','')})

    result = {
        "approved_task_hash": _task_definition_hash(
            tasks_from_plan(state["plan"], IMPLEMENTATION_RELATIVE_PATH)
        ) if approved else "",
        "approved_requirements_hash": state.get('requirements_hash','') if approved else '',
        "change_request": change_request if decision == 'request_changes' else '',
        "task_plan_valid": state.get("task_plan_valid", False) if approved else False,
        "approval": decision,
        "status": "APPROVED" if approved else "REQUIREMENT_CHANGES_REQUIRED" if decision == 'request_changes' else "REJECTED"
    }
    if approved and state.get('plan_lineage_required'):
        from governance.plan_lineage import authorize
        try:result.update(authorize({**state,**result}))
        except Exception as error:
            log_event(state['workflow_id'],'plan_lineage','ENGINEERING_PLAN_AUTHORIZATION_REJECTED',{'error':str(error)})
            result.update({'approval':'blocked','approved_task_hash':'','plan_authorization':{},
                           'status':'STOPPED_PLAN_LINEAGE'})
    return result


# Decide which path to follow
def approval_router(state: EngineeringState):
    if state.get('status')=='STOPPED_PLAN_LINEAGE':return 'blocked'
    if state.get('approval') == 'request_changes':
        return 'changes'
    if state["approval"] == "approve":
        return "approved"

    return "rejected"


def approved_node(state: EngineeringState):
    print("\n[5] PLAN APPROVED")
    log_event(
        workflow_id=state["workflow_id"],
        agent="human_approval",
        event="PLAN_APPROVED",
        details={"decision": "approve", "requirement_version":state.get('requirement_version',0),
                 "requirements_hash":state.get('requirements_hash',''),
                 "approved_task_hash":state.get('approved_task_hash','')},
    )

    return {
        "status": "READY_FOR_IMPLEMENTATION"
    }


def task_scheduler_node(state: EngineeringState):
    print("\n[TASK SCHEDULER]")
    if state.get("approval") != "approve":
        return {"task_plan_valid": False, "status": "STOPPED"}

    assert_plan_current(state, approved=True)
    try:
        scheduled, ready, parallel_batches = schedule_tasks(state["tasks"])
    except TaskPlanError as error:
        log_event(
            workflow_id=state["workflow_id"],
            agent="task_scheduler",
            event="TASK_SCHEDULING_FAILED",
            details={"error_type": type(error).__name__},
        )
        return {
            "task_plan_valid": False,
            "status": "STOPPED_INVALID_TASK_PLAN",
        }

    if not ready:
        log_event(
            workflow_id=state["workflow_id"],
            agent="task_scheduler",
            event="TASK_SCHEDULING_FAILED",
            details={"error_type": "NoEligibleTasks"},
        )
        return {
            "task_plan_valid": False,
            "status": "STOPPED_NO_ELIGIBLE_TASKS",
        }

    active = [ready[0]]
    log_event(
        workflow_id=state["workflow_id"],
        agent="task_scheduler",
        event="TASKS_SCHEDULED",
        details={
            "ready_task_ids": ready,
            "active_task_ids": active,
            "parallel_batches": parallel_batches,
        },
    )
    return {
        "tasks": scheduled,
        "active_task_ids": active,
        "task_parallel_batches": parallel_batches,
        "task_plan_valid": True,
        "status": "TASKS_READY",
    }


def task_scheduler_router(state: EngineeringState):
    if not state.get("task_plan_valid"):
        return "failure"
    validate_task_plan(state["tasks"])
    active = [task for task in state["tasks"]
              if task["task_id"] in state.get("active_task_ids", [])]
    if any(task.get("execution_type", "implementation") == "validate_only" for task in active):
        _require_approved_validation_plan(state)
        if len(active) != 1:
            raise TaskPlanError("Validation-only tasks must execute separately")
        return "verification"
    return "implementation"


def _java_source_hashes(workspace: Path) -> dict[str, str]:
    root = workspace.resolve(strict=True)
    source = (root / "url-shortener/src").resolve(strict=True)
    if not source.is_relative_to(root):
        raise PermissionError("Java source escapes isolated workspace")
    hashes = {}
    for path in sorted(source.rglob("*.java")):
        if not path.resolve(strict=True).is_relative_to(root):
            raise PermissionError("Java source file escapes isolated workspace")
        hashes[path.relative_to(root).as_posix()] = file_hash(path.read_bytes())
    return hashes


def verification_node(state: EngineeringState):
    """Start the supported read-only task; validation determines its outcome."""
    if state.get("approval") != "approve" or not state.get("task_plan_valid"):
        raise PermissionError("Verification requires an approved, scheduled task")
    _require_approved_validation_plan(state)
    selected = [
        task for task in state["tasks"]
        if task["task_id"] in state.get("active_task_ids", [])
    ]
    if (
        state.get("active_task_ids") != ["verify-expiration"]
        or len(selected) != 1
        or selected[0].get("execution_type") != "validate_only"
        or selected[0]["allowed_files"] != [IMPLEMENTATION_RELATIVE_PATH]
    ):
        raise TaskPlanError("Unsupported verification task")
    hashes = _java_source_hashes(Path(state["workspace"]))
    if IMPLEMENTATION_RELATIVE_PATH not in hashes:
        raise FileNotFoundError("Verification target ShortUrl.java is missing")
    running_tasks = start_tasks(state["tasks"], state["active_task_ids"])
    print("[VERIFICATION] Existing Java source found; source files changed: 0")
    log_event(
        workflow_id=state["workflow_id"],
        agent="task_scheduler",
        event="VERIFICATION_STARTED",
        details={
            "task_ids": state["active_task_ids"],
            "source_changes": False,
            "source_files_changed": 0,
            "skipped_stages": ["source_generation", "test_generation", "repair", "documentation", "readiness"],
            "skip_reason": "Validation-only plan asserts existing implementation satisfies requirements without modification",
        },
    )
    return {
        "tasks": running_tasks,
        "validation_only": True,
        "verification_source_hashes": hashes,
        "source_files_changed": 0,
        "status": "READY_FOR_VALIDATION",
    }


def implementation_node(state: EngineeringState):
    print("\n[IMPLEMENTATION AGENT]")

    if (
        state["approval"] != "approve"
        or not state.get("task_plan_valid")
        or not state.get("active_task_ids")
    ):
        return {"status": "STOPPED"}

    assert_plan_current(state, approved=True)
    if any(
        task.get("execution_type", "implementation") == "validate_only"
        for task in state["tasks"] if task["task_id"] in state["active_task_ids"]
    ):
        raise PermissionError("Validation-only tasks cannot invoke implementation")

    try:
        running_tasks = start_tasks(state["tasks"], state["active_task_ids"])
        log_event(
            workflow_id=state["workflow_id"],
            agent="task_scheduler",
            event="TASKS_STARTED",
            details={"task_ids": state["active_task_ids"]},
        )
    except TaskPlanError as error:
        log_event(
            workflow_id=state["workflow_id"],
            agent="task_scheduler",
            event="TASK_EXECUTION_FAILED",
            details={"error_type": type(error).__name__},
        )
        return {"task_plan_valid": False, "status": "STOPPED_TASK_NOT_READY"}

    selected = [task for task in state["tasks"] if task["task_id"] in state["active_task_ids"]]
    if len(selected) != 1:
        raise TaskPlanError("Implementation requires exactly one approved task at a time")
    if state.get("approved_task_hash") != task_definition_hash(state["tasks"]):
        raise PermissionError("Implementation requires the unchanged human-approved task plan")
    approved_task = selected[0]
    log_event(workflow_id=state['workflow_id'], agent='implementation_agent', event='IMPLEMENTATION_ATTEMPT_STARTED', details=
              {'attempt': state.get('retry_count', 0) + 1, 'task_ids': state['active_task_ids']})
    try:
        transaction = implement_task(
            task=approved_task, workspace=Path(state["workspace"]), context=state,
        )
    except Exception as error:
        if isinstance(error, ModelUnavailableError) or getattr(error, 'status', None) == 'STOPPED_MODEL_UNAVAILABLE':
            log_event(
                workflow_id=state["workflow_id"], agent="implementation_agent",
                event="MODEL_UNAVAILABLE", details={"stage": "implementation", "error_type": type(error).__name__, "error": str(error)},
            )
            return {"implementation_error": str(error),
                    "implementation_error_type": type(error).__name__,
                    "status": "STOPPED_MODEL_UNAVAILABLE"}
        log_event(
            workflow_id=state["workflow_id"], agent="implementation_agent",
            event="IMPLEMENTATION_BLOCKED", details={"error_type": type(error).__name__, "error": str(error)},
        )
        return {"implementation_error": str(error),
                "implementation_error_type": type(error).__name__,
                "implementation_attempt_details": getattr(error, 'attempt_details', {}),
                "implementation_proposal": getattr(error, 'attempt_details', {}).get('proposal', {}),
                "status": "STOPPED_ROLLBACK_REQUIRES_REVIEW" if isinstance(error, RecoveryRequired)
                else "STOPPED_IMPLEMENTATION_BLOCKED"}
    paths = transaction["paths"]
    checkpoints = transaction["checkpoints"]
    changed = bool(paths)
    log_event(
        workflow_id=state["workflow_id"], agent="implementation_agent",
        event="IMPLEMENTATION_COMPLETED",
        details={"file_changed": changed, "paths": paths, "task_id": approved_task["task_id"],
                 "transaction_id": transaction["transaction_id"],
                 "attempt": state.get("retry_count", 0) + 1,
                 "checkpoints": [{"path": cp["path"], "original_hash": cp["original_hash"],
                    "checkpoint_id": transaction["transaction_id"] + ":" + cp["path"]} for cp in checkpoints]},
    )
    return {
        "implementation_error": "", "implementation_error_type": "",
        "implementation_attempt_details": {},
        "implementation_proposal": transaction.get('proposal', {}),
        "implementation_file": str(Path(state["workspace"]) / paths[0]),
        "implementation_transaction": transaction,
        "source_files_changed": len(paths),
        "rollback_allowed_files": approved_task["allowed_files"],
        "validation_only": False,
        "rollback_checkpoint": checkpoints[0],
        "implementation_changed": changed,
        "expected_current_hash": transaction["expected_hashes"][paths[0]],
        "tasks": running_tasks, "status": "IMPLEMENTATION_COMPLETED",
    }


def validation_node(state: EngineeringState):
    print("\n[VALIDATION AGENT]")

    try:
        result = validate_implementation(Path(state["workspace"]))
    except Exception as error:
        result = {"passed": False, "exit_code": -1, "duration_seconds": 0,
                  "output": f"Validation could not run: {type(error).__name__}: {error}"}
    if result["passed"] and state.get("implementation_transaction"):
        try:
            finish_transaction(state["implementation_transaction"]["transaction_id"],
                               Path(state["workspace"]), state["rollback_allowed_files"], passed=True)
        except Exception as error:
            result = {**result, "passed": False,
                      "output": result.get("output", "") + f"\nSource verification failed: {error}"}

    source_updates = {}
    if state.get("validation_only"):
        before = state["verification_source_hashes"]
        after = _java_source_hashes(Path(state["workspace"]))
        changed = sum(before.get(path) != after.get(path) for path in before.keys() | after.keys())
        source_updates["source_files_changed"] = changed
        print(f"[VALIDATION] Source files changed: {changed}")
        if changed:
            result = {**result, "passed": False,
                      "output": result.get("output", "") + "\nJava source changed during validation."}
    log_event(
        workflow_id=state["workflow_id"],
        agent="validation_agent",
        event="VALIDATION_PASSED" if result["passed"] else "VALIDATION_FAILED",
        details={
            "exit_code": result["exit_code"],
            "duration_seconds": result["duration_seconds"],
            "retry_count": state.get("retry_count", 0),
            "repair_applied": state.get("repair_applied", False),
        },
    )

    updates: dict[str, object] = {
        **source_updates,
        "validation_result": result,
        "status": "VALIDATION_PASSED"
        if result["passed"]
        else "VALIDATION_FAILED"
    }

    if state.get("active_task_ids") and state.get("tasks"):
        try:
            updates["tasks"] = finish_tasks(
                state["tasks"],
                state["active_task_ids"],
                result["passed"],
            )
            log_event(
                workflow_id=state["workflow_id"],
                agent="task_scheduler",
                event="TASKS_COMPLETED" if result["passed"] else "TASKS_FAILED",
                details={"task_ids": state["active_task_ids"]},
            )
        except TaskPlanError as error:
            log_event(
                workflow_id=state["workflow_id"],
                agent="task_scheduler",
                event="TASK_EXECUTION_FAILED",
                details={"error_type": type(error).__name__},
            )
            updates["task_plan_valid"] = False

    if (
        not result["passed"]
        and state.get("repair_applied")
        and not state.get("repair_rolled_back")
    ):
        try:
            rollback(
                state["repair_checkpoint"],
                state["repair_expected_current_hash"],
                Path(state["workspace"]),
            )
            log_event(
                workflow_id=state["workflow_id"],
                agent="rollback_manager",
                event="REPAIR_ROLLBACK_COMPLETED",
                details={"path": state["repair_checkpoint"]["path"]},
            )
            updates.update(
                {
                    "repair_applied": False,
                    "repair_rolled_back": True,
                    "repair_rollback_blocked": False,
                }
            )
        except Exception as error:
            log_event(
                workflow_id=state["workflow_id"],
                agent="rollback_manager",
                event="REPAIR_ROLLBACK_BLOCKED",
                details={"error_type": type(error).__name__},
            )
            updates["repair_rollback_blocked"] = True

    return updates


MAX_RETRIES = 2



def retry_node(state: EngineeringState):
    retry_count = state.get("retry_count", 0) + 1
    print(f"\n[RETRY] Attempt {retry_count}/{MAX_RETRIES}")
    error_log = state["validation_result"].get("output", "")
    if error_log.strip():
        proposal = propose_repair(error_log, Path(state["workspace"]))
    else:
        proposal = "Insufficient validation evidence. No repair proposed."
    repair_change = parse_repair_proposal(proposal)
    log_event(
        workflow_id=state["workflow_id"],
        agent="repair_agent",
        event="REPAIR_PROPOSED",
        details={
            "retry_count": retry_count,
            "proposal_available": bool(proposal.strip()),
            "repair_change_available": repair_change is not None,
        },
    )

    return {
        "retry_count": retry_count,
        "repair_proposal": proposal,
        "repair_change": repair_change or {},
        "status": "REPAIR_REVIEW_REQUIRED",
    }


def repair_review_node(state: EngineeringState):
    from langgraph.types import interrupt

    print("\n[REPAIR REVIEW] Human approval required")
    print("Proposed repair:\n", state["repair_proposal"])
    decision = interrupt({
        "type": "REPAIR_APPROVAL",
        "proposal": state["repair_proposal"],
        "retry_count": state["retry_count"],
        "message": "Approve or reject the proposed repair.",
    })
    approved = str(decision).strip().lower() == "approve"
    log_event(
        workflow_id=state["workflow_id"],
        agent="human_repair_review",
        event="REPAIR_APPROVED" if approved else "REPAIR_REJECTED",
        details={"retry_count": state["retry_count"]},
    )
    return {
        "repair_approval": "approve" if approved else "reject"
    }


def repair_approval_router(state: EngineeringState):
    if state.get("repair_approval") == "approve" and state.get("repair_change"):
        return "approved"
    return "rejected"


def repair_executor_node(state: EngineeringState):
    print("\n[REPAIR EXECUTOR]")
    if state.get("repair_approval") != "approve":
        return {"status": "STOPPED_REPAIR_NOT_APPLIED"}

    change = state.get("repair_change") or {}
    try:
        running_tasks = None
        if state.get("tasks"):
            running_tasks = start_tasks(
                state["tasks"], state.get("active_task_ids", [])
            )
        from governance.policy_engine import workflow_policy_context
        with workflow_policy_context(state['workflow_id'], state.get('policy_approvals', ())):
            result = apply_approved_repair(change, Path(state["workspace"]))
    except Exception as error:
        log_event(
            workflow_id=state["workflow_id"],
            agent="repair_executor",
            event="REPAIR_EXECUTION_BLOCKED",
            details={"error_type": type(error).__name__},
        )
        return {"status": "STOPPED_REPAIR_NOT_APPLIED"}

    log_event(
        workflow_id=state["workflow_id"],
        agent="repair_executor",
        event="REPAIR_EXECUTED",
        details={"path": result["path"], "file_changed": result["changed"]},
    )
    updates: dict[str, object] = {
        "repair_checkpoint": result["checkpoint"],
        "repair_expected_current_hash": result["expected_current_hash"],
        "repair_applied": True,
        "repair_rolled_back": False,
        "repair_rollback_blocked": False,
        "status": "REPAIR_APPLIED",
    }
    if running_tasks is not None:
        updates["tasks"] = running_tasks
    return updates


def repair_execution_router(state: EngineeringState):
    return "validation" if state.get("repair_applied") else "stopped"


def repair_stopped_node(state: EngineeringState):
    print("\n[REPAIR] Awaiting separate implementation authorization.")
    return {"status": "STOPPED_REPAIR_NOT_APPLIED"}


def quality_gate(state: EngineeringState):
    if state["validation_result"]["passed"]:
        if any(
            task["status"] in {"PENDING", "READY", "RUNNING"}
            for task in state.get("tasks", [])
        ):
            return "tasks"
        return "passed"

    if state.get("validation_only"):
        return "failed"
    if state.get("implementation_transaction") and state.get("recovery_enabled", True):
        return "recover"
    if state.get("rollback_allowed_files"):
        return "failed"

    retry_count = state.get("retry_count", 0)
    if state.get("repair_rollback_blocked"):
        return "failed"
    if state.get("replan_attempted") and not state.get("replan_meaningful_change"):
        return "failed"
    if retry_count < MAX_RETRIES:
        return "replan"

    return "failed"


def implementation_outcome_router(state):
    if state.get('status') == 'IMPLEMENTATION_COMPLETED':
        return 'validation'
    if state.get('status') in {'STOPPED_IMPLEMENTATION_BLOCKED', 'STOPPED_ROLLBACK_REQUIRES_REVIEW'} and state.get('recovery_enabled', True):
        return 'recover'
    return 'stopped'


def failure_recovery_node(state: EngineeringState):
    """Collect evidence, restore the whole set, then decide whether retry is safe."""
    evidence = collect_failure(state)
    transaction = state.get('implementation_transaction', {})
    rolled_back = False
    if transaction:
        try:
            finish_transaction(transaction['transaction_id'], Path(state['workspace']),
                               state['rollback_allowed_files'], passed=False)
            rolled_back = True
            log_event(workflow_id=state['workflow_id'], agent='rollback_manager', event='ROLLBACK_COMPLETED', details=
                      {'attempt': evidence['attempt'], 'paths': transaction['paths'],
                       'transaction_id': transaction['transaction_id']})
        except Exception as error:
            log_event(workflow_id=state['workflow_id'], agent='rollback_manager', event='ROLLBACK_BLOCKED', details=
                      {'attempt': evidence['attempt'], 'error_type': type(error).__name__})
            observed = collect_failure(state)['observed_hashes']
            evidence.update(category='source_edit_conflict', recoverable=False,
                            rolled_back=False, rollback_error=f'{type(error).__name__}: {error}',
                            restored_hashes=observed)
            evidence['evidence_hash'] = file_hash(json.dumps(
                {key: value for key, value in evidence.items() if key != 'evidence_hash'}, sort_keys=True).encode())
            log_event(workflow_id=state['workflow_id'], agent='failure_classifier', event='FAILURE_CLASSIFIED',
                      details={'attempt': evidence['attempt'], 'category': 'source_edit_conflict', 'recoverable': False,
                               'evidence_hash': evidence['evidence_hash']})
            log_event(workflow_id=state['workflow_id'], agent='diagnostic_collector', event='DIAGNOSTICS_COLLECTED',
                      details={'attempt': evidence['attempt'], 'rollback_error': evidence['rollback_error'],
                               'observed_hashes': observed})
            return {'failure_context': evidence, 'attempt_history': state.get('attempt_history', []) + [evidence],
                    'source_files_changed': sum(observed.get(path) != value for path, value in evidence['original_hashes'].items()),
                    'recovery_ready': False, 'recovery_stop_reason': 'ROLLBACK_BLOCKED',
                    'status': 'STOPPED_ROLLBACK_REQUIRES_REVIEW'}
    after = collect_failure(state)['observed_hashes']
    evidence['restored_hashes'] = after
    evidence['rolled_back'] = rolled_back
    evidence['evidence_hash'] = file_hash(json.dumps(
        {key: value for key, value in evidence.items() if key != 'evidence_hash'}, sort_keys=True).encode())
    history = state.get('attempt_history', []) + [evidence]
    log_event(workflow_id=state['workflow_id'], agent='failure_classifier', event='FAILURE_CLASSIFIED', details=
              {'attempt': evidence['attempt'], 'category': evidence['category'],
               'recoverable': evidence['recoverable'], 'evidence_hash': evidence['evidence_hash']})
    log_event(workflow_id=state['workflow_id'], agent='diagnostic_collector', event='DIAGNOSTICS_COLLECTED', details=
              {'attempt': evidence['attempt'], 'proposal_available': bool(evidence['attempted_edits']),
               'original_hashes': evidence['original_hashes'], 'restored_hashes': after,
               'test_failure_count': len(evidence['test_failures']), 'transaction_id': evidence['transaction_id']})
    reason = ''
    if not evidence['recoverable']:
        reason = 'NONRECOVERABLE_' + evidence['category'].upper()
    elif state.get('retry_count', 0) >= MAX_RETRIES:
        reason = 'RETRY_LIMIT_EXHAUSTED'
    elif evidence['original_hashes'] and any(after.get(path) != value for path, value in evidence['original_hashes'].items()):
        reason = 'SOURCE_HASH_MISMATCH'
    if reason:
        log_event(workflow_id=state['workflow_id'], agent='recovery_controller', event='RECOVERY_STOPPED', details=
                  {'attempt': evidence['attempt'], 'reason': reason})
    return {'failure_context': evidence, 'attempt_history': history, 'recovery_ready': not reason,
            'recovery_stop_reason': reason,
            'source_files_changed': sum(after.get(path) != value for path, value in evidence['original_hashes'].items()),
            'status': 'READY_FOR_RECOVERY_PLAN' if not reason else
                      ('STOPPED_ROLLBACK_REQUIRES_REVIEW' if state.get('status') == 'STOPPED_ROLLBACK_REQUIRES_REVIEW'
                       else 'STOPPED_ROLLED_BACK' if rolled_back else 'STOPPED_IMPLEMENTATION_BLOCKED')}


def recovery_planning_node(state: EngineeringState):
    assert_plan_current(state, approved=True)
    evidence = state['failure_context']
    definitions = [{key: value for key, value in task.items() if key != 'status'} for task in state['tasks']]
    prompt = f"""You are the recovery replanning agent for a Java workflow.
Analyze the structured failure evidence. Do not execute commands, edit files,
weaken tests, change Java/build versions, or switch to validation-only execution.
Return ONLY JSON with exactly tasks, change_summary, correction_strategy.
tasks must be the full structured task list using the current schema, same task IDs
and dependencies. Preserve the requirement. Keep unchanged task definitions when
only implementation details need correction. A material change to a task description,
files, or execution type will require fresh human approval before generation.
All tasks must remain implementation tasks for existing Java files. Do not change
completed tasks. No new files. correction_strategy must target the actual evidence.

Original requirement: {state['requirement']}
Current tasks: {json.dumps(definitions)}
Failure evidence: {json.dumps(evidence)}
Output schema (preserve these task definitions unless a material revision is necessary):
{json.dumps({'tasks': definitions, 'change_summary': 'Describe the evidence-based correction', 'correction_strategy': 'Explain the precise regeneration fix without weakening validation'})}
"""
    try:
        payload = json.loads(_response_text(model.invoke(prompt, stage='planning')))
        if not isinstance(payload, dict) or set(payload) != {'tasks', 'change_summary', 'correction_strategy'}:
            raise TaskPlanError('Recovery response requires tasks, change_summary, correction_strategy')
        if not all(isinstance(payload[key], str) and payload[key].strip() for key in ('change_summary', 'correction_strategy')):
            raise TaskPlanError('Recovery strategy and summary must be non-empty strings')
        candidate = tasks_from_plan(json.dumps({'tasks': payload['tasks']}), IMPLEMENTATION_RELATIVE_PATH, strict=True)
        previous = {task['task_id']: task for task in state['tasks']}
        if {task['task_id'] for task in candidate} != set(previous):
            raise TaskPlanError('Recovery cannot add or remove task IDs')
        for task in candidate:
            old = previous[task['task_id']]
            if not isinstance(task['description'], str) or not task['description'].strip() or not task['allowed_files']:
                raise TaskPlanError('Recovery tasks require descriptions and existing files')
            if task['dependencies'] != old['dependencies'] or task.get('execution_type') != 'implementation':
                raise TaskPlanError('Recovery cannot change task dependencies or bypass implementation')
            if old['status'] == 'COMPLETED' and task_definition_hash([dict(task, dependencies=[])]) != task_definition_hash([dict(old, dependencies=[])]):
                raise TaskPlanError('Recovery cannot change completed tasks')
            for path in task['allowed_files']:
                authorized_java_target(path, Path(state['workspace']), task['allowed_files'])
            if old['status'] == 'COMPLETED':
                task['status'] = 'COMPLETED'
        material = task_definition_hash(candidate) != state['approved_task_hash']
    except Exception as error:
        log_event(workflow_id=state['workflow_id'], agent='recovery_replanner', event='RECOVERY_PLAN_BLOCKED', details=
                  {'attempt': evidence['attempt'], 'error_type': type(error).__name__})
        return {'recovery_ready': False, 'recovery_approved': False,
                'recovery_stop_reason': 'INVALID_RECOVERY_PLAN', 'status': 'STOPPED_INVALID_RECOVERY_PLAN'}
    version = state.get('plan_version', 1) + 1
    entry = {'version': version, 'source': 'autonomous_recovery', 'tasks': candidate,
             'change_summary': payload['change_summary'], 'correction_strategy': payload['correction_strategy'],
             'material_change': material, 'failure_attempt': evidence['attempt']}
    log_event(workflow_id=state['workflow_id'], agent='recovery_replanner', event='RECOVERY_PLAN_CREATED', details=
              {'plan_version': version, 'material_change': material, 'attempt': evidence['attempt'],
               'evidence_hash': evidence['evidence_hash']})
    return {'recovery_candidate_tasks': candidate, 'recovery_material_change': material,
            'recovery_correction_strategy': payload['correction_strategy'],
            'recovery_change_summary': payload['change_summary'], 'plan_version': version,
            'replan_history': state.get('replan_history', []) + [entry],
            'recovery_ready': True, 'recovery_approved': False, 'status': 'RECOVERY_PLAN_READY'}


def recovery_review_node(state: EngineeringState):
    approved = True
    if state['recovery_material_change']:
        decision = interrupt({'type': 'RECOVERY_PLAN_APPROVAL', 'plan_version': state['plan_version'],
                              'tasks': state['recovery_candidate_tasks'],
                              'change_summary': state['recovery_change_summary'],
                              'correction_strategy': state['recovery_correction_strategy'],
                              'failure_category': state['failure_context']['category'],
                              'message': 'Review the materially revised recovery task plan.'})
        approved = str(decision).strip().lower() == 'approve'
    event = 'RECOVERY_PLAN_APPROVED' if approved else 'RECOVERY_PLAN_REJECTED'
    log_event(workflow_id=state['workflow_id'], agent='human_recovery_review' if state['recovery_material_change'] else 'recovery_controller',
              event=event, details= {'plan_version': state['plan_version'], 'material_change': state['recovery_material_change'],
                      'approval_basis': 'human' if state['recovery_material_change'] else 'unchanged_approved_tasks'})
    return {'recovery_approved': approved,
            'approved_task_hash': task_definition_hash(state['recovery_candidate_tasks']) if approved else state['approved_task_hash'],
            'status': 'RECOVERY_APPROVED' if approved else 'STOPPED_RECOVERY_REJECTED'}


def recovery_retry_node(state: EngineeringState):
    if not state.get('recovery_approved'):
        return {'status': 'STOPPED_RECOVERY_NOT_APPROVED', 'recovery_ready': False}
    if state.get('retry_count', 0) >= MAX_RETRIES:
        return {'status': 'STOPPED_RETRY_LIMIT', 'recovery_ready': False}
    if state.get('plan_lineage_required'):
        # Recovery constructs a different raw plan below. Its old authorization
        # cannot cover that new artifact, even after a task-only review.
        log_event(state['workflow_id'],'plan_lineage','RECOVERY_REAUTHORIZATION_REQUIRED',
                  {'old_authorization_hash':state.get('plan_authorization',{}).get('authorization_hash'),
                   'candidate_task_hash':task_definition_hash(state['recovery_candidate_tasks'])})
        return {'status':'STOPPED_RECOVERY_REAUTHORIZATION_REQUIRED','recovery_ready':False,
                'plan_authorization':{},'approved_task_hash':'','approval':''}
    tasks = state['recovery_candidate_tasks']
    if task_definition_hash(tasks) != state['approved_task_hash']:
        raise PermissionError('Recovery task plan changed after approval')
    scheduled, ready, batches = schedule_tasks(tasks)
    retry_ids = state['active_task_ids']
    if not retry_ids or not set(retry_ids).issubset(ready):
        return {'status': 'STOPPED_NO_ELIGIBLE_TASKS', 'recovery_ready': False}
    count = state.get('retry_count', 0) + 1
    evidence = state['failure_context']
    log_event(workflow_id=state['workflow_id'], agent='recovery_controller', event='IMPLEMENTATION_RETRY_STARTED', details=
              {'retry_count': count, 'attempt': count + 1, 'plan_version': state['plan_version']})
    return {'retry_count': count, 'tasks': scheduled, 'active_task_ids': retry_ids,
            'task_parallel_batches': batches, 'task_plan_valid': True,
            'plan': json.dumps({'tasks': [{key: value for key, value in task.items() if key != 'status'} for task in tasks]}),
            'previous_implementation_proposal': evidence['attempted_edits'],
            'previous_validation_result': evidence['validation'],
            'previous_implementation_error': evidence['message'],
            'implementation_transaction': {}, 'implementation_proposal': {},
            'implementation_attempt_details': {}, 'implementation_error': '', 'implementation_error_type': '',
            'validation_result': {}, 'rollback_checkpoint': {}, 'rollback_allowed_files': [],
            'implementation_changed': False, 'recovery_ready': True, 'status': 'READY_FOR_IMPLEMENTATION_RETRY'}


def success_node(state: EngineeringState):
    print("\n[QUALITY GATE] Validation passed")
    log_event(
        workflow_id=state["workflow_id"],
        agent="quality_gate",
        event="AWAITING_FINAL_HUMAN_REVIEW",
        details={"source_files_changed": state["source_files_changed"]}
        if state.get("validation_only") else {},
    )

    updates = {
        "status": "AWAITING_FINAL_HUMAN_REVIEW"
    }
    if state.get("final_human_review_required"):
        bundle = compute_final_review_bundle(state)
        updates["final_review_bundle_hash"] = bundle["bundle_hash"]
        updates["final_review_artifact_hashes"] = bundle["source_hashes"]
    return updates


def compute_final_review_bundle(state: EngineeringState) -> dict:
    workspace = Path(state['workspace'])
    source_hashes = _java_source_hashes(workspace) if (workspace / "url-shortener/src").exists() else {}
    for doc_candidate in [workspace / "engineering-notes.md", workspace / "docs"]:
        if doc_candidate.is_file():
            source_hashes[doc_candidate.relative_to(workspace).as_posix()] = file_hash(doc_candidate.read_bytes())
        elif doc_candidate.is_dir():
            for doc_file in sorted(doc_candidate.rglob("*.md")):
                source_hashes[doc_file.relative_to(workspace).as_posix()] = file_hash(doc_file.read_bytes())
    plan_version = state.get('plan_version', 1)
    approved_task_hash = state.get('approved_task_hash', '')
    validation_exit_code = state.get('validation_result', {}).get('exit_code', 0)
    bundle_data = {
        'workflow_id': state['workflow_id'],
        'plan_version': plan_version,
        'approved_task_hash': approved_task_hash,
        'source_hashes': source_hashes,
        'validation_exit_code': validation_exit_code,
    }
    bundle_hash = hashlib.sha256(json.dumps(bundle_data, sort_keys=True).encode()).hexdigest()
    return {
        'source_hashes': source_hashes,
        'bundle_hash': bundle_hash,
        'plan_version': plan_version,
    }


def final_human_review_node(state: EngineeringState):
    initial_bundle_hash = state.get("final_review_bundle_hash")
    initial_artifact_hashes = state.get("final_review_artifact_hashes")
    if not initial_bundle_hash or initial_artifact_hashes is None:
        bundle = compute_final_review_bundle(state)
        initial_bundle_hash = bundle["bundle_hash"]
        initial_artifact_hashes = bundle["source_hashes"]

    plan_version = state.get("plan_version", 1)

    is_parallel = bool(state.get("parallel_result"))
    has_test_gen = bool(state.get("test_hash") or state.get("integrated_result", {}).get("test_hash"))
    has_docs = bool(state.get("integrated_result", {}).get("documentation_hash"))
    has_readiness = bool(state.get("integrated_result", {}).get("readiness_report"))
    complete_sdlc = has_test_gen and has_docs and has_readiness
    is_val_only = state.get("validation_only") or any(t.get("execution_type") == "validate_only" for t in state.get("tasks", []))

    summary = {
        "requirement": state.get("requirement", ""),
        "validation_passed": state.get("validation_result", {}).get("passed", True),
        "maven_exit_code": state.get("validation_result", {}).get("exit_code", 0),
        "source_files_changed": state.get("source_files_changed", 0),
    }
    details_extra = {}
    if is_parallel and not complete_sdlc:
        summary["complete_sdlc"] = False
        summary["sdlc_scope"] = "parallel_implementation_and_validation"
        summary["unexecuted_stages"] = ["additive_test_generation", "documentation_generation", "release_readiness_evaluation"]
        details_extra = {
            "complete_sdlc": False,
            "sdlc_scope": "parallel_implementation_and_validation",
            "unexecuted_stages": ["additive_test_generation", "documentation_generation", "release_readiness_evaluation"],
            "parallel_reporting_notice": (
                "Parallel execution covers concurrent task implementation and joined Maven validation. "
                "It is NOT a complete SDLC workflow: additive test generation, documentation, and readiness were not executed."
            ),
        }
    elif is_val_only:
        summary["validation_only"] = True
        summary["skipped_stages"] = ["source_generation", "test_generation", "repair", "documentation", "readiness"]
        summary["skip_reason"] = "Validation-only plan asserts existing implementation satisfies requirements without modification"
        details_extra = {
            "validation_only": True,
            "skipped_stages": ["source_generation", "test_generation", "repair", "documentation", "readiness"],
            "skip_reason": "Validation-only plan asserts existing implementation satisfies requirements without modification",
        }

    log_event(
        workflow_id=state["workflow_id"],
        agent="final_human_review",
        event="FINAL_REVIEW_REQUESTED",
        details={
            "workflow_id": state["workflow_id"],
            "plan_version": plan_version,
            "bundle_hash": initial_bundle_hash,
            "source_hashes": initial_artifact_hashes,
            "source_files_changed": state.get("source_files_changed", 0),
            "disclaimer": (
                "Engineering approval records acceptance that the implementation satisfies requirements "
                "and passes all validation gates. It does NOT authorize production release or deployment."
            ),
            **details_extra,
        }
    )
    interrupt_payload = {
        "type": "FINAL_HUMAN_REVIEW",
        "workflow_id": state["workflow_id"],
        "plan_version": plan_version,
        "bundle_hash": initial_bundle_hash,
        "source_hashes": initial_artifact_hashes,
        "summary": summary,
        "disclaimer": (
            "Engineering approval records acceptance that the implementation satisfies requirements "
            "and passes all validation gates. It does NOT authorize production release or deployment."
        ),
    }
    if is_parallel and not complete_sdlc:
        interrupt_payload["complete_sdlc"] = False
        interrupt_payload["sdlc_scope"] = "parallel_implementation_and_validation"
        interrupt_payload["unexecuted_stages"] = ["additive_test_generation", "documentation_generation", "release_readiness_evaluation"]
    elif is_val_only:
        interrupt_payload["validation_only"] = True
        interrupt_payload["skipped_stages"] = ["source_generation", "test_generation", "repair", "documentation", "readiness"]

    decision = interrupt(interrupt_payload)

    current_bundle = compute_final_review_bundle(state)
    if current_bundle["source_hashes"] != initial_artifact_hashes or current_bundle["bundle_hash"] != initial_bundle_hash:
        print("\n[FINAL HUMAN REVIEW] STALE APPROVAL DETECTED — Artifacts changed after review was requested.")
        log_event(
            workflow_id=state["workflow_id"],
            agent="final_human_review",
            event="FINAL_REVIEW_REJECTED",
            details={
                "workflow_id": state["workflow_id"],
                "decision": "reject",
                "reason": "stale_artifact_hash",
                "original_bundle_hash": initial_bundle_hash,
                "current_bundle_hash": current_bundle["bundle_hash"],
                "engineering_accepted": False,
                "production_release_authorized": False,
            }
        )
        return {
            "status": "STOPPED_FINAL_REVIEW_REJECTED",
            "final_review_decision": "reject",
            "engineering_accepted": False,
            "production_release_authorized": False,
            "final_review_error": "Stale review: underlying artifacts changed after review request",
            "final_review_bundle_hash": current_bundle["bundle_hash"],
            "final_review_artifact_hashes": current_bundle["source_hashes"],
        }

    if decision == "approve":
        log_event(
            workflow_id=state["workflow_id"],
            agent="final_human_review",
            event="FINAL_REVIEW_APPROVED",
            details={
                "workflow_id": state["workflow_id"],
                "decision": "approve",
                "plan_version": plan_version,
                "bundle_hash": initial_bundle_hash,
                "source_hashes": initial_artifact_hashes,
                "engineering_accepted": True,
                "production_release_authorized": False,
                **details_extra,
            }
        )
        return {
            "status": "ENGINEERING_ACCEPTED",
            "final_review_decision": "approve",
            "engineering_accepted": True,
            "production_release_authorized": False,
            "final_review_bundle_hash": initial_bundle_hash,
            "final_review_artifact_hashes": initial_artifact_hashes,
        }
    else:
        log_event(
            workflow_id=state["workflow_id"],
            agent="final_human_review",
            event="FINAL_REVIEW_REJECTED",
            details={
                "workflow_id": state["workflow_id"],
                "decision": "reject",
                "plan_version": plan_version,
                "bundle_hash": initial_bundle_hash,
                "engineering_accepted": False,
                "production_release_authorized": False,
            }
        )
        return {
            "status": "STOPPED_FINAL_REVIEW_REJECTED",
            "final_review_decision": "reject",
            "engineering_accepted": False,
            "production_release_authorized": False,
            "final_review_bundle_hash": initial_bundle_hash,
            "final_review_artifact_hashes": initial_artifact_hashes,
        }


def final_review_router(state: EngineeringState):
    if state.get("status") == "ENGINEERING_ACCEPTED":
        return "accepted"
    return "rejected"


def final_review_accepted_node(state: EngineeringState):
    print("\n[FINAL HUMAN REVIEW] Approved — Engineering Acceptance Recorded")
    print("NOTE: Production release authorization is NOT granted.")
    return {
        "status": "ENGINEERING_ACCEPTED",
        "engineering_accepted": True,
        "production_release_authorized": False,
    }


def final_review_rejected_node(state: EngineeringState):
    print("\n[FINAL HUMAN REVIEW] Rejected — Workflow Stopped")
    return {
        "status": "STOPPED_FINAL_REVIEW_REJECTED",
        "engineering_accepted": False,
        "production_release_authorized": False,
    }


def success_router(state: EngineeringState):
    if state.get("final_human_review_required"):
        return "final_human_review"
    return "end"


def failure_node(state: EngineeringState):
    print("\n[QUALITY GATE] Validation failed")

    if state.get("repair_rollback_blocked"):
        print("[ROLLBACK] Repair rollback requires review.")
        return {
            "status": "STOPPED_ROLLBACK_REQUIRES_REVIEW"
        }

    if not state.get("implementation_changed", False):
        print("[ROLLBACK] No new AI changes. Nothing to restore.")
        log_event(
            workflow_id=state["workflow_id"],
            agent="rollback_manager",
            event="VALIDATION_STOPPED",
            details={"rollback_needed": False},
        )
        return {
            "status": "STOPPED_VALIDATION_FAILED"
        }

    checkpoint = state["rollback_checkpoint"]
    expected_hash = state["expected_current_hash"]
    try:
        if state.get("implementation_transaction"):
            finish_transaction(state["implementation_transaction"]["transaction_id"],
                               Path(state["workspace"]), state["rollback_allowed_files"], passed=False)
        else:
            rollback(checkpoint, expected_hash, Path(state["workspace"]),
                     allowed_files=state.get("rollback_allowed_files"))
        print("[ROLLBACK] AI-generated changes restored.")
        log_event(
            workflow_id=state["workflow_id"],
            agent="rollback_manager",
            event="ROLLBACK_COMPLETED",
            details={"rollback_needed": True,
                     "paths": state.get("implementation_transaction", {}).get("paths", [checkpoint["path"]])},
        )
        return {
            "source_files_changed": 0,
            "status": "STOPPED_ROLLED_BACK"
        }
    except Exception as error:
        print(f"[ROLLBACK] Failed safely: {error}")
        log_event(
            workflow_id=state["workflow_id"],
            agent="rollback_manager",
            event="ROLLBACK_BLOCKED",
            details={"error_type": type(error).__name__},
        )
        return {
            "status": "STOPPED_ROLLBACK_REQUIRES_REVIEW"
        }


def rejected_node(state: EngineeringState):
    print("\n[5] PLAN REJECTED — SAFE STOP")
    log_event(
        workflow_id=state["workflow_id"],
        agent="human_approval",
        event="PLAN_REJECTED",
        details={"decision": "reject"},
    )

    return {
        "status": "STOPPED"
    }


def parallel_node(state):
    from governance.parallel_execution import build_workflow
    evidence_dir = state.get('parallel_evidence')
    if not evidence_dir:
        evidence_dir = str(Path(state['workspace']).parent / f"{state['workflow_id']}-parallel-evidence")
        Path(evidence_dir).mkdir(parents=True, exist_ok=True)
    import inspect
    sig = inspect.signature(build_workflow)
    kwargs = {}
    if 'downstream' in sig.parameters:
        kwargs['downstream'] = state.get('downstream', True)
    result = build_workflow(model, **kwargs).compile().invoke({
        'context': dict(state), 'evidence': evidence_dir,
        'cache': state.get('integration_cache', '')})
    is_complete = bool(result.get('test_hash') and (result.get('document') or result.get('documentation_hash')) and result.get('readiness'))
    if is_complete:
        return {'parallel_result': result, 'status': result['status'],
                'complete_sdlc': True,
                'sdlc_scope': 'complete_sdlc',
                'unexecuted_stages': [],
                'test_hash': result.get('test_hash'),
                'document': result.get('document'),
                'readiness': result.get('readiness'),
                'source_files_changed': sum(len(t.get('paths', [])) for t in result.get('transactions', []))}
    return {'parallel_result': result, 'status': result['status'],
            'complete_sdlc': False,
            'sdlc_scope': 'parallel_implementation_and_validation',
            'unexecuted_stages': ['additive_test_generation', 'documentation_generation', 'release_readiness_evaluation'],
            'source_files_changed': len(result.get('transaction', {}).get('paths', []))
                if result['status'] == 'AWAITING_PARALLEL_RESULTS_REVIEW' else 0}


def integrated_node(state):
    from governance.integrated_sdlc import build_workflow
    evidence_dir = state.get('integration_evidence')
    if not evidence_dir:
        evidence_dir = str(Path(state['workspace']).parent / f"{state['workflow_id']}-integrated-evidence")
    Path(evidence_dir).mkdir(parents=True, exist_ok=True)
    context={**state,'implementation_response_format':state.get('implementation_response_format','complete_files')}
    initial={'context':context,'evidence':evidence_dir,'cache':state.get('integration_cache', '')}
    if state.get('integrated_correction_state'):
        # Validate inside the checkpointed correction_entry node, once. This
        # wrapper replays when nested human interrupts resume, after source writes.
        initial=state['integrated_correction_state']
    if state.get('integrated_test_replay_state'):
        initial=state['integrated_test_replay_state']
    if state.get('integrated_correction_state') or state.get('integrated_test_replay_state'):
        from governance.plan_lineage import approved
        if approved(state)!=approved(initial['context']):
            raise PermissionError('Continuation cannot use another requirement/design/plan authorization')
    result=build_workflow(model).compile().invoke(initial)
    txs = result.get('transactions', [])
    paths_changed = set()
    for tx in txs:
        paths_changed.update(tx.get('paths', []))
    if not paths_changed and result.get('transaction'):
        paths_changed.update(result['transaction'].get('paths', []))
    return {
        'integrated_result': result,
        'status': result['status'],
        'source_files_changed': len(paths_changed) if result.get('recovery') != 'rolled_back' else 0
    }


def execution_strategy_router(state: EngineeringState):
    tasks = state.get('tasks', [])
    is_val_only = state.get('validation_only') or any(t.get('execution_type') == 'validate_only' for t in tasks)

    # 1. Validation-only plans bypass source modification and route to verification/validation
    if is_val_only:
        print("\n[ORCHESTRATION] Validation-only task plan detected -> Verification & Validation Selected (Source & Test Generation Skipped)")
        log_event(
            workflow_id=state.get('workflow_id', ''),
            agent='orchestrator',
            event='VALIDATION_ONLY_STAGES_SKIPPED',
            details={
                'strategy': 'validation_only',
                'skipped_stages': ['source_generation', 'test_generation', 'repair', 'documentation', 'readiness'],
                'reason': 'Validation-only plan asserts existing implementation satisfies requirements without modification',
            }
        )
        return 'legacy'

    # 2. Explicit legacy option keeps legacy task scheduler accessible
    if state.get('legacy'):
        print("\n[ORCHESTRATION] Explicit --legacy execution requested -> Legacy Task Scheduler Selected")
        return 'legacy'

    # 3. Explicit flags take precedence if supplied
    if state.get('parallel_evidence'):
        return 'parallel'
    if state.get('integrated_sdlc'):
        return 'integrated'

    # 4. Auto orchestration:
    # If parallel eligible -> parallel execution
    # Otherwise, for ordinary sequential source-changing workflows -> integrated_sdlc is default!
    if state.get('auto_orchestration', True):
        eligible, reason = is_parallel_eligible(tasks)
        if eligible:
            print(f"\n[ORCHESTRATION] {reason} -> Multi-Wave Parallel Execution Selected")
            return 'parallel'
        else:
            print(f"\n[ORCHESTRATION] {reason} -> Defaulting to Integrated SDLC (Sequential Full Lifecycle)")
            return 'integrated'

    # 5. If auto_orchestration is disabled and no explicit legacy option given:
    # Ordinary sequential source-changing workflows still default to integrated_sdlc
    return 'integrated'


# Build LangGraph workflow
workflow = StateGraph(EngineeringState)

workflow.add_node("requirements", requirements_agent)
workflow.add_node("clarification", clarification_node)
workflow.add_node("contract_review", contract_review_node)
workflow.add_node("design", design_node)
workflow.add_node("design_review", design_review_node)
workflow.add_node("requirement_changes", requirement_changes_node)
workflow.add_node("codebase", codebase_agent)
workflow.add_node("planning", planning_agent)
workflow.add_node("task_scheduler", task_scheduler_node)
workflow.add_node('parallel_execution', parallel_node)
workflow.add_node("replanning", replanning_node)
workflow.add_node("replan_review", replan_review_node)
workflow.add_node("replan_stopped", replan_stopped_node)
workflow.add_node("approval", human_approval)
workflow.add_node("approved", approved_node)
workflow.add_node('integrated_sdlc',integrated_node)
workflow.add_node("rejected", rejected_node)
workflow.add_node("implementation", implementation_node)
workflow.add_node("verification", verification_node)
workflow.add_node("failure_recovery", failure_recovery_node)
workflow.add_node("recovery_planning", recovery_planning_node)
workflow.add_node("recovery_review", recovery_review_node)
workflow.add_node("recovery_retry", recovery_retry_node)
workflow.add_node("validation", validation_node)
workflow.add_node("retry", retry_node)
workflow.add_node("repair_review", repair_review_node)
workflow.add_node("repair_executor", repair_executor_node)
workflow.add_node("repair_stopped", repair_stopped_node)
workflow.add_node("success", success_node)
workflow.add_node("failure", failure_node)
workflow.add_node("final_human_review", final_human_review_node)
workflow.add_node("final_review_accepted", final_review_accepted_node)
workflow.add_node("final_review_rejected", final_review_rejected_node)

workflow.add_edge(START, "requirements")
workflow.add_conditional_edges('requirements', requirements_router,
                               {'stop':END,'clarify':'clarification','codebase':'codebase'})
workflow.add_conditional_edges('clarification', clarification_router,
                               {'stop':END,'clarify':'clarification','codebase':'codebase'})
workflow.add_conditional_edges('requirement_changes',
    lambda state: 'requirements' if state.get('requirement_change_ready') else
                  'changes' if state.get('status')=='REQUIREMENT_CHANGES_REQUIRED' else 'stop',
    {'requirements':'requirements','changes':'requirement_changes','stop':END})
workflow.add_edge("codebase", "contract_review")
workflow.add_conditional_edges('contract_review',
    lambda state: 'stop' if state.get('status') in {'STOPPED_CONTRACT_REJECTED','STOPPED_CONTRACT_REQUIRED','STOPPED_CONTRACT_INVALID'}
                  else 'design' if state.get('design_required') else 'plan',
    {'stop':END, 'plan':'planning','design':'design'})
workflow.add_conditional_edges('design',
    lambda state:'stop' if state.get('status') in {'STOPPED_DESIGN_INVALID', 'STOPPED_MODEL_UNAVAILABLE'} else
                 'retry' if state.get('status')=='DESIGN_RETRY' else
                 'review' if state.get('design_valid') else 'stop',
    {'retry':'design','review':'design_review','stop':END})
workflow.add_conditional_edges('design_review',
    lambda state:'plan' if state.get('status')=='DESIGN_APPROVED' else
                 'correct' if state.get('status')=='DESIGN_CHANGES_REQUESTED' else
                 'review' if state.get('status')=='DESIGN_REVIEW_INVALID' else 'stop',
    {'plan':'planning','correct':'design','review':'design_review','stop':END})
workflow.add_conditional_edges('planning',
    lambda state: 'retry' if state.get('status') == 'SEMANTIC_PLAN_RETRY' else
                  'stop' if state.get('status') in {'STOPPED_MODEL_UNAVAILABLE', 'STOPPED_SEMANTIC_PLAN_INVALID'} else 'approve',
    {'retry':'planning','stop':END,'approve':'approval'})

workflow.add_conditional_edges(
    "approval",
    approval_router,
    {
        "approved": "approved",
        "rejected": "rejected",
        "blocked": END,
        "changes": "requirement_changes"
    }
)

def route_parallel_outcome(state: EngineeringState):
    if state.get('status') in {'AWAITING_PARALLEL_RESULTS_REVIEW', 'AWAITING_FINAL_HUMAN_REVIEW'} and state.get('final_human_review_required', True):
        return 'final_human_review'
    return 'end'

def route_integrated_outcome(state: EngineeringState):
    if state.get('status') == 'AWAITING_FINAL_HUMAN_REVIEW' and state.get('final_human_review_required', True):
        return 'final_human_review'
    return 'end'

workflow.add_conditional_edges('approved', execution_strategy_router,
                               {'parallel': 'parallel_execution', 'integrated': 'integrated_sdlc', 'legacy': 'task_scheduler'})
workflow.add_conditional_edges('parallel_execution', route_parallel_outcome,
                               {'final_human_review': 'final_human_review', 'end': END})
workflow.add_conditional_edges('integrated_sdlc', route_integrated_outcome,
                               {'final_human_review': 'final_human_review', 'end': END})
workflow.add_conditional_edges(
    "task_scheduler",
    task_scheduler_router,
    {"implementation": "implementation", "verification": "verification", "failure": "failure"},
)
workflow.add_edge("rejected", END)
workflow.add_conditional_edges(
    "implementation",
    implementation_outcome_router,
    {"validation": "validation", "recover": "failure_recovery", "stopped": END},
)
workflow.add_edge("verification", "validation")

workflow.add_conditional_edges(
    "validation",
    quality_gate,
    {
        "passed": "success",
        "tasks": "task_scheduler",
        "replan": "replanning",
        "failed": "failure",
        "recover": "failure_recovery",
    },
)

workflow.add_conditional_edges('failure_recovery', lambda state: 'plan' if state.get('recovery_ready') else 'stop',
                               {'plan': 'recovery_planning', 'stop': END})
workflow.add_conditional_edges('recovery_planning', lambda state: 'review' if state.get('recovery_ready') else 'stop',
                               {'review': 'recovery_review', 'stop': END})
workflow.add_conditional_edges('recovery_review', lambda state: 'retry' if state.get('recovery_approved') else 'stop',
                               {'retry': 'recovery_retry', 'stop': END})
workflow.add_conditional_edges('recovery_retry', lambda state: 'run' if state.get('recovery_ready') else 'stop',
                               {'run': 'implementation', 'stop': END})

workflow.add_conditional_edges(
    "success",
    success_router,
    {"final_human_review": "final_human_review", "end": END}
)
workflow.add_conditional_edges(
    "final_human_review",
    final_review_router,
    {"accepted": "final_review_accepted", "rejected": "final_review_rejected"}
)
workflow.add_edge("final_review_accepted", END)
workflow.add_edge("final_review_rejected", END)
workflow.add_edge("failure", END)
workflow.add_edge("replanning", "replan_review")
workflow.add_conditional_edges(
    "replan_review",
    replan_approval_router,
    {"approved": "retry", "rejected": "replan_stopped"},
)
workflow.add_edge("retry", "repair_review")
workflow.add_conditional_edges(
    "repair_review",
    repair_approval_router,
    {"approved": "repair_executor", "rejected": "repair_stopped"},
)
workflow.add_conditional_edges(
    "repair_executor",
    repair_execution_router,
    {"validation": "validation", "stopped": "repair_stopped"},
)
workflow.add_edge("repair_stopped", END)


def main(obligation_contract=None, design_required=False, plan_lineage_required=False,
         integration_evidence=None, integration_cache=None, parallel_evidence=None,
         final_review_required=False, auto_orchestration=True, full_sdlc=False, legacy=False,
         skip_startup_validation=False):
    if parallel_evidence and (integration_evidence or plan_lineage_required or not (design_required and obligation_contract and integration_cache)):
        raise ValueError('Parallel execution requires design, contract and Maven cache; sequential lineage/integration cannot be reused')
    if integration_evidence and not (plan_lineage_required and design_required and obligation_contract and integration_cache):
        raise ValueError('Integrated SDLC requires design, contract, lineage and Maven cache')
    if not skip_startup_validation:
        from governance.startup_validation import validate_environment
        validate_environment(
            contract=obligation_contract,
            maven_cache=integration_cache,
        )
    if integration_evidence:
        integration_evidence=Path(integration_evidence).resolve()
        integration_evidence.mkdir(parents=True,exist_ok=False)
    print("\nSCHWAB AGENTIC SDLC SYSTEM")
    print("==========================")

    requirement = input("\nEnter requirement: ").strip()

    if not requirement:
        print("Requirement cannot be empty.")
        return

    workflow_id = str(uuid.uuid4())

    workspace = initialize_workspace(workflow_id)
    log_event(
        workflow_id=workflow_id, agent="cli", event="WORKFLOW_STARTED",
        details={"workspace": str(workspace)},
    )

    config = {
        "configurable": {
            "thread_id": workflow_id
        }
    }

    # Persist checkpoints in SQLite
    with SqliteSaver.from_conn_string("workflow.db") as checkpointer:

        app = workflow.compile(checkpointer=checkpointer)

        initial_state = {
            'engineering_accepted': False,
            'production_release_authorized': False,
            'final_review_decision': '',
            'final_review_bundle_hash': '',
            'final_review_artifact_hashes': {},
            'final_human_review_required': final_review_required or full_sdlc,
            'final_review_error': '',
            'parallel_evidence': str(parallel_evidence or ''),
            'integrated_sdlc': bool(integration_evidence or not legacy),
            'integration_evidence': str(integration_evidence or ''),
            'integration_cache': str(integration_cache or ''),
            'auto_orchestration': auto_orchestration,
            'full_sdlc': full_sdlc or (not legacy),
            'legacy': legacy,
            "plan_lineage_required": plan_lineage_required,
            "design_required": design_required,
            "obligation_contract": obligation_contract or {},
            "semantic_required": obligation_contract is not None,
            "workflow_id": workflow_id,
            "workspace": str(workspace),
            "requirement": requirement,
            "requirement_version": 1,
            "requirement_history": [],
            "clarification_history": [],
            "analysis": "",
            "codebase_analysis": "",
            "plan": "",
            "approval": "",
            "approved_task_hash": "",
            "source_files_changed": 0,
            "verification_source_hashes": {},
            "status": "STARTED",
            "implementation_file": "",
            "validation_result": {},
            "repair_proposal": "",
            "repair_approval": "",
            "retry_count": 0,
            "recovery_enabled": True,
            "attempt_history": [],
            "rollback_checkpoint": {},
            "validation_only": False,
            "implementation_changed": False,
            "expected_current_hash": "",
            "repair_change": {},
            "repair_checkpoint": {},
            "repair_expected_current_hash": "",
            "repair_applied": False,
            "repair_rolled_back": False,
            "repair_rollback_blocked": False,
            "plan_version": 0,
            "revised_plan": "",
            "plan_change_summary": "",
            "replan_context": {},
            "replan_history": [],
            "replan_approval": "",
            "replan_meaningful_change": True,
            "replan_attempted": False,
            "revised_tasks": [],
            "tasks": [],
            "active_task_ids": [],
            "task_plan_valid": False,
            "task_parallel_batches": [],
        }

        result = app.invoke(initial_state, config=config)

        # Handle both plan approval and repair approval interrupts.
        while "__interrupt__" in result:
            interrupts = result["__interrupt__"]

            if not interrupts:
                break

            payload = interrupts[0].value
            if isinstance(payload,dict) and payload.get('type') in {'PARALLEL_TASK_APPROVAL','INTEGRATED_TEST_GENERATION_APPROVAL','TEST_RESULTS_REVIEW','FINAL_RELEASE_REVIEW'}:
                print(json.dumps(payload,indent=2))
                decision='approve' if input('Review (1 approve / 2 reject): ').strip()=='1' else 'reject'
                result=app.invoke(Command(resume=decision),config=config)
                continue

            if isinstance(payload,dict) and payload.get('type') == 'FINAL_HUMAN_REVIEW':
                print('\n========== FINAL HUMAN REVIEW ==========')
                print('Workflow ID:', payload.get('workflow_id'))
                print('Plan version:', payload.get('plan_version'))
                print('Bundle hash:', payload.get('bundle_hash'))
                print('Summary:', json.dumps(payload.get('summary', {}), indent=2))
                print('\nDISCLAIMER:', payload.get('disclaimer'))
                choice = input('Review (1 approve / 2 reject): ').strip()
                decision = 'approve' if choice == '1' else 'reject'
                result = app.invoke(Command(resume=decision), config=config)
                continue

            if isinstance(payload,dict) and payload.get('type')=='REQUIREMENT_CLARIFICATION':
                print("\n========== REQUIREMENT CLARIFICATION ==========")
                for error in payload.get('errors',[]): print("Answer error:",error)
                answers={}
                for question in payload['questions']:
                    print(f"{question['id']}: {question['question']}")
                    for index,option in enumerate(question['options'],1): print(f"{index}. {option}")
                    if question['default_assumption'] is not None:
                        print("Unconfirmed suggestion:",question['default_assumption'])
                    answer=input("Answer (option number or text): ").strip()
                    if answer.isdigit() and 1 <= int(answer) <= len(question['options']):
                        answer=question['options'][int(answer)-1]
                    answers[question['id']]=answer
                decision='approve' if input("Approve these answers? (1 approve / 2 reject): ").strip()=='1' else 'reject'
                result=app.invoke(Command(resume={'decision':decision,'answers':answers}),config=config)
                continue
            if isinstance(payload,dict) and payload.get('type')=='REQUIREMENT_CHANGES':
                print("\n========== REVISE REQUIREMENT ==========")
                for error in payload.get('errors',[]): print("Change error:",error)
                requirement_update=input("Revised requirement (blank to cancel): ").strip()
                response={'decision':'submit','requirement':requirement_update} if requirement_update else {'decision':'cancel'}
                result=app.invoke(Command(resume=response),config=config)
                continue
            if isinstance(payload,dict) and payload.get('type')=='REQUIREMENT_CONTRACT_APPROVAL':
                print("\n========== REQUIREMENT OBLIGATION CONTRACT ==========")
                print(json.dumps(payload['contract'],indent=2))
                decision='approve' if input('Approve obligations only? (1 approve / 2 reject): ').strip()=='1' else 'reject'
                result=app.invoke(Command(resume=decision),config=config)
                continue
            if isinstance(payload,dict) and payload.get('type')=='DESIGN_APPROVAL':
                print('\n========== ARCHITECTURE / DESIGN REVIEW ==========')
                print(json.dumps(payload['artifact'],indent=2))
                for error in payload.get('errors',[]):print('Review error:',error)
                choice=input('Design review (1 approve / 2 reject / 3 request changes): ').strip()
                decision=({'decision':'request_changes','feedback':input('Required design corrections: ').strip()}
                          if choice=='3' else 'approve' if choice=='1' else 'reject')
                result=app.invoke(Command(resume=decision),config=config)
                continue

            if isinstance(payload, dict) and payload.get("type") == "RECOVERY_PLAN_APPROVAL":
                print("\n========== RECOVERY PLAN APPROVAL ==========")
                print("Plan version:", payload["plan_version"])
                print("Failure category:", payload["failure_category"])
                print("Changes:", payload["change_summary"])
                print("Strategy:", payload["correction_strategy"])
                print("Tasks:", json.dumps(payload["tasks"], indent=2))
                print("\n1. Approve recovery plan")
                print("2. Reject recovery plan")
            elif isinstance(payload, dict) and payload.get("type") == "REPLAN_APPROVAL":
                print("\n========== REPLAN APPROVAL ==========")
                print("Plan version:", payload.get("plan_version", 0))
                print("Changes:", payload.get("change_summary", ""))
                print("Revised plan:", payload.get("revised_plan", ""))
                print("\n1. Approve revised plan")
                print("2. Reject revised plan")
            elif isinstance(payload, dict) and payload.get("type") == "REPAIR_APPROVAL":
                print("\n========== REPAIR APPROVAL ==========")
                print("Proposal:", payload.get("proposal", ""))
                print("Retry:", payload.get("retry_count", 0))
                print("\n1. Approve proposal")
                print("2. Reject proposal")
            else:
                print("\n========== PLAN APPROVAL ==========")
                print("Requirement:", payload.get("requirement", ""))
                print("Plan:\n", payload.get("plan", ""))
                print("\n1. Approve")
                print("2. Reject")
                print("3. Request requirement changes")

            choice = input("\nEnter decision (1/2): ").strip()

            # Reject unexpected input rather than treating it as approval.
            decision = 'approve' if choice == '1' else 'request_changes' if choice == '3' and isinstance(payload,dict) and 'plan' in payload else 'reject'

            result = app.invoke(
                Command(resume=decision),
                config=config,
            )

        log_event(
            workflow_id=workflow_id, agent="cli", event="WORKFLOW_FINISHED",
            details={"status": result["status"],
                     "validation_passed": result.get("validation_result", {}).get("passed"),
                     "source_files_changed": result.get("source_files_changed")},
        )
        print("\n========== FINAL RESULT ==========")
        print("Workflow ID:", workflow_id)
        print("Requirement:", result["requirement"])
        print("Approval:", result["approval"])
        print("Status:", result["status"])
        if result.get("requirements_error"):
            print("Requirements error:",result["requirements_error"])
        if "source_files_changed" in result:
            print("Source files changed:", result["source_files_changed"])
        if result.get("validation_result"):
            print("Maven exit code:", result["validation_result"]["exit_code"])
            print("Validation passed:", result["validation_result"]["passed"])
        return result


if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser()
    parser.add_argument('--contract-file', type=Path,
                        help='Explicit machine-checkable obligations; reviewed before planning')
    parser.add_argument('--design', action='store_true', help='Require gated architecture/design and human design approval')
    parser.add_argument('--plan-lineage', action='store_true', help='Require prospective plan/execution receipt bindings')
    parser.add_argument('--integration-evidence', type=Path, help='New external directory for sequential SDLC evidence')
    parser.add_argument('--maven-cache', type=Path, help='Existing offline Maven dependency cache')
    parser.add_argument('--parallel-evidence', type=Path, help='Opt-in independent-task parallel proposal execution; requires design, contract and Maven cache')
    parser.add_argument('--final-review', action='store_true', help='Require interactive checkpointed final human review after validation')
    parser.add_argument('--full-sdlc', action='store_true', help='Execute complete integrated SDLC lifecycle')
    parser.add_argument('--verify-audit', nargs='?', const=str(audit_logger.DB_PATH),
                        help='Verify cryptographic integrity of audit ledger (default: agent-orchestrator/audit.db)')
    parser.add_argument('--legacy', action='store_true', help='Use legacy task scheduler execution instead of integrated SDLC')
    parser.add_argument('--demo', choices=['greenfield', 'brownfield', 'ambiguous'], help='Run offline fixture replay demonstration')
    parser.add_argument('--no-auto-orchestration', dest='auto_orchestration', action='store_false', help='Disable automatic parallel orchestration selection')
    parser.set_defaults(auto_orchestration=True)
    args = parser.parse_args()

    if args.verify_audit:
        from governance.audit_logger import verify_audit_integrity_readonly
        db_path = Path(args.verify_audit)
        valid, report = verify_audit_integrity_readonly(db_path)
        if valid:
            print(f"AUDIT CHAIN INTEGRITY: VALID (records={report['total_records']}, head_id={report.get('head_id')})")
            sys.exit(0)
        else:
            print(f"AUDIT CHAIN INTEGRITY: FAILED - {report.get('error')}: {report.get('message')}", file=sys.stderr)
            sys.exit(1)

    from governance.startup_validation import validate_environment, StartupValidationError

    if args.demo:
        try:
            validate_environment(is_demo=True)
        except StartupValidationError as e:
            print(f"\nSTARTUP VALIDATION FAILED:\n{e}\n", file=sys.stderr)
            sys.exit(1)
        if args.demo == 'greenfield':
            from governance.offline_demos import run_greenfield_demo
            run_greenfield_demo()
        elif args.demo == 'brownfield':
            from governance.offline_demos import run_brownfield_demo
            run_brownfield_demo()
        elif args.demo == 'ambiguous':
            from governance.offline_demos import run_ambiguous_demo
            run_ambiguous_demo()
        sys.exit(0)

    if args.plan_lineage and (not args.design or not args.contract_file):
        parser.error('--plan-lineage requires --design and --contract-file')

    contract_data = None
    if args.contract_file:
        try:
            contract_data = json.loads(args.contract_file.read_text())
        except Exception as e:
            print(f"Failed to read contract file '{args.contract_file}': {e}", file=sys.stderr)
            sys.exit(1)

    try:
        validate_environment(
            contract=contract_data,
            maven_cache=args.maven_cache,
            is_demo=False,
        )
    except StartupValidationError as e:
        print(f"\nSTARTUP VALIDATION FAILED:\n{e}\n", file=sys.stderr)
        sys.exit(1)

    main(contract_data,
         design_required=args.design, plan_lineage_required=args.plan_lineage,
         integration_evidence=args.integration_evidence, integration_cache=args.maven_cache,
         parallel_evidence=args.parallel_evidence,
         final_review_required=args.final_review,
         auto_orchestration=args.auto_orchestration,
         full_sdlc=args.full_sdlc,
         legacy=args.legacy,
         skip_startup_validation=True)
