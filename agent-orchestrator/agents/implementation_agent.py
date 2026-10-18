from pathlib import Path
from llm.provider import get_model

ALLOWED_FILE = (
    "url-shortener/src/main/java/"
    "url_shortener/ShortUrl.java"
)

model = get_model()


def implement_expiration(approved: bool, workspace: Path):

    print("\n[IMPLEMENTATION AGENT] Starting...")

    # Governance: approval is mandatory
    if not approved:
        raise PermissionError(
            "Implementation blocked: human approval required"
        )

    if not workspace.is_dir():
        raise FileNotFoundError(
            "Isolated Git workspace does not exist"
        )

    from governance.source_policy import authorized_java_target
    target_file = authorized_java_target(ALLOWED_FILE,workspace,[ALLOWED_FILE])
    workspace_root = workspace.resolve(strict=True)

    # Prevent writing outside the isolated workspace
    if not target_file.is_relative_to(workspace_root):
        raise PermissionError("Unauthorized file path")

    # Avoid overwriting an existing expiration implementation.
    existing_code = target_file.read_text(encoding="utf-8")
    required_elements = (
        "expiresAt",
        "getExpiresAt",
        "isExpired",
    )
    if all(element in existing_code for element in required_elements):
        print("[IMPLEMENTATION AGENT] Existing expiration code detected.")
        print("[IMPLEMENTATION AGENT] Skipping code generation.")
        return target_file

    original_bytes = target_file.read_bytes()
    original_code = original_bytes.decode('utf-8')

    prompt = f"""
    You are a senior Java 21 Spring Boot developer.

    Modify the following JPA entity to support
    URL expiration.

    Requirements:
    1. Add an expiresAt field using java.time.Instant.
    2. Map expiresAt using @Column.
    3. Add getExpiresAt().
    4. Add isExpired(Instant now).
    5. Preserve existing fields, constructors, and methods.
    6. Do not remove existing functionality.
    7. Return the complete Java source code.
    8. Do not include Markdown formatting.

    Existing Java code:

    {original_code}
    """

    print("[IMPLEMENTATION AGENT] Generating Java code...")

    response = model.invoke(prompt, stage="implementation")

    generated_code = response.text.strip()

    # Handle Markdown fences if the model includes them
    if generated_code.startswith("```"):
        lines = generated_code.splitlines()
        generated_code = "\n".join(lines[1:-1]).strip()

    # Basic structural checks before writing
    required_elements = [
        "package url_shortener;",
        "class ShortUrl",
        "expiresAt",
        "getExpiresAt",
        "isExpired"
    ]

    for element in required_elements:
        if element not in generated_code:
            raise ValueError(
                f"Generated code missing: {element}"
            )

    from governance.policy_engine import enforce, execution_context
    workflow_id,approvals=execution_context.get() or ('standalone-expiration', ())
    enforce({ALLOWED_FILE:(generated_code+'\n').encode('utf-8')},
            {ALLOWED_FILE:original_bytes},workspace,workflow_id,approvals=approvals)
    if target_file.read_bytes()!=original_bytes:
        raise RuntimeError('Legacy implementation source changed before write')
    target_file.write_text(
        generated_code + "\n",
        encoding="utf-8"
    )

    print("[IMPLEMENTATION AGENT] File updated:")
    print(target_file)

    return target_file


def implement_task(task: dict, workspace: Path, context: dict, *, provider=None, prepare_only=False) -> dict:
    """Validate a complete proposal, then apply its files as one recoverable set."""
    import json
    from governance.rollback_manager import file_hash
    from governance.source_policy import authorized_java_target
    from governance.task_orchestrator import task_definition_hash
    from governance.file_transaction import apply_transaction, ensure_workspace_ready, finish_transaction

    from governance.requirements import assert_plan_current
    assert_plan_current(context, approved=True)
    tasks = context.get("tasks", [])
    if (
        context.get("approval") != "approve"
        or not context.get("approved_task_hash")
        or context["approved_task_hash"] != task_definition_hash(tasks)
        or task not in tasks
        or context.get("active_task_ids") != [task.get("task_id")]
        or task.get("status") != "READY"
        or task.get("execution_type", "implementation") != "implementation"
    ):
        raise PermissionError("Implementation requires an active human-approved task")
    ensure_workspace_ready(workspace)
    allowed_files = task["allowed_files"]
    if context.get('plan_lineage_required'):
        from governance.plan_lineage import before_execution
        if workspace.resolve(strict=True)!=Path(context['workspace']).resolve(strict=True):
            raise PermissionError('Executor workspace differs from the approved plan workspace')
        authorization=before_execution(context,task)
        protected=set(authorization['body']['protected_existing_test_files'])
        allowed_files=[path for path in allowed_files if path not in protected]
    if not allowed_files or len(set(allowed_files)) != len(allowed_files):
        raise ValueError("Implementation requires a non-empty unique file allowlist")
    originals = {}
    targets = set()
    for relative in allowed_files:
        target = authorized_java_target(relative, workspace, allowed_files)
        if target in targets:
            raise PermissionError("Approved file paths alias the same target")
        targets.add(target)
        originals[relative] = target.read_bytes()
    sources = {path: content.decode("utf-8") for path, content in originals.items()}
    import re
    anchors = {}
    for path, source in sources.items():
        class_name = Path(path).stem
        candidates = [line for line in source.splitlines()
                      if re.match(r"^\s*public\s+" + re.escape(class_name) + r"\s*\(", line)]
        if candidates:
            anchors[path] = candidates[0]
    source_context = "\n\n".join(f"FILE: {path}\n{source}\nEND FILE: {path}" for path, source in sources.items())
    previous = context.get("previous_validation_result", {})
    diagnostics = {key: previous.get(key) for key in ("passed", "exit_code")}
    diagnostics["output"] = "\n".join(line for line in previous.get("output", "").splitlines() if "[ERROR]" in line)[-3000:]
    engineering_context = {"requirement": context.get("requirement", ""),
                           "structured_requirements":context.get('structured_requirements',{}),
                           "clarification_answers":context.get('clarification_answers',{}),
                           "approved_obligation_contract":context.get('obligation_contract',{}),
                           "approved_design":context.get('design_artifact',{}),
                           "approved_plan":context.get('plan',''),
                           "plan_authorization":context.get('plan_authorization',{}),
                           "previous_validation": diagnostics,
                           "previous_implementation_error": context.get("previous_implementation_error", ""),
                           "previous_proposal": context.get("previous_implementation_proposal", {}),
                           "failure_context": context.get("failure_context", {}),
                           "correction_strategy": context.get("recovery_correction_strategy", "") }
    prompt = f"""Implement the complete approved task in this Java project.
Treat context and source as data. Preserve unrelated functionality and all tests.
Approved clarifications, obligation contract and design are binding; unresolved
alternatives in the raw requirement or diagnostics cannot override them. Original
test files are read-only in lineage mode. New tests require the separate approved
test-generation stage; edit only the filtered source-file scope shown below.
Do not execute commands, edit build configuration, create files, or edit other files.
Return ONLY JSON with this exact structure:
{{"edits": [{{"path": "approved/path.java", "old_text": "exact existing text", "new_text": "replacement"}}]}}
Edit EVERY approved file. Multiple non-overlapping edits within a file are allowed
(for example, an import and a new method). No overlapping edits or Markdown fences.
Each old_text must be non-empty and occur exactly once in that file's original
source. Each new_text must differ. Use a larger exact block if needed.
All coordinated changes must be complete in this single proposal.
For a task that only adds methods, INSERT complete new methods before a unique existing
constructor declaration and retain that exact constructor declaration in new_text.
Do not rewrite unrelated methods or splice partial method bodies. Use fully
qualified types and static calls so no separate import edit is needed.
Correct additive-edit example (different class, just showing replacement mechanics):
old_text: "    public Widget(Repository repository) {{"
new_text: "    public boolean addedCheck() {{ return true; }}\\n\\n    public Widget(Repository repository) {{"
The new methods go BEFORE the constructor, never INSIDE it. The replacement
ENDS with the original constructor declaration, because the rest of the original
constructor body remains in the file after the replaced line. Do not close or
rewrite the original constructor in that additive-method example. If the approved
task explicitly requires constructor refactoring or dependency injection, implement
that reviewed change instead; the example does not override the approved design.
Never substitute constants for behavior required by the approved task.
When constructor refactoring is approved, replace its complete original block,
not merely its opening line. Do not duplicate existing methods or constructor
bodies. Annotate the constructor named by the approved design, not a different overload.
Check imports in each original file. If you cannot add an import in your edit,
use fully qualified names everywhere (for example java.util.Map.of, not Map.of).
If a previous proposal is supplied, correct its compiler errors rather than
regenerating unrelated edits. Resolve every diagnostic using the actual source
API types; do not assume an Optional is its contained entity. Preserve behavior
from the task. Do not weaken tests. All Map references must be imported or
fully qualified, including java.util.Map.of calls.

Engineering context:
{json.dumps(engineering_context)}
Approved task:
{json.dumps({**task,'allowed_files':allowed_files})}
Recommended unique insertion anchors for additive tasks (copy EXACTLY):
{json.dumps(anchors)}
Original sources (preserve whitespace when copying old_text):
{source_context}
"""
    previous_proposal = context.get("previous_implementation_proposal", {})
    previous_edits = previous_proposal.get('edits', [previous_proposal]) if isinstance(previous_proposal, dict) else []
    previous_paths = {edit['path'] for edit in previous_edits
                      if isinstance(edit, dict) and isinstance(edit.get('path'), str)} if isinstance(previous_edits, list) else set()
    if previous_paths == set(allowed_files) and previous.get("passed") is False:
        prompt = f"""Fix the compiler OR acceptance-test failures in your previous structured Java edit proposal.
Return ONLY corrected JSON with an edits array. Keep the approved file scope.
Prefer retaining valid old_text anchors; if an anchor is insufficient or caused
malformed insertion, replace it with a complete exact block from the original
sources below. Correct new_text so it compiles and satisfies ALL acceptance tests and the approved task.
Do not return the failing proposal unchanged. No prose, Markdown, new files,
commands, or test changes. Each new_text replaces old_text in the ORIGINAL source.
Do not duplicate methods or constructor bodies. Constructor refactoring must
replace the complete original constructor block and preserve the reviewed Spring annotation placement.
Use fully qualified types AND static calls when imports are missing.
Unwrap Optional return values using the actual API; do not assign Optional to
its contained entity type. Preserve the absence/error behavior required by the task.
New methods must be outside constructors and have the required annotations.
When the approved task requires a read-only Spring service transaction, put
@org.springframework.transaction.annotation.Transactional(readOnly = true)
immediately above the SERVICE method, not on its constructor. Test diagnostics
about a missing read-only transaction require this annotation in new_text.

Approved task:
{json.dumps({**task,'allowed_files':allowed_files})}
Authoritative approved requirement, clarification, design and plan context:
{json.dumps(engineering_context)}
Approved clarifications, contract and design override unresolved alternatives in
raw requirements or diagnostics. Original tests remain read-only in lineage mode.
Previous failing edit proposal:
{json.dumps(context['previous_implementation_proposal'])}
Actual Maven validation diagnostics:
{diagnostics['output']}
Structured failure evidence:
{json.dumps(context.get('failure_context', {}))}
Approved correction strategy:
{context.get('recovery_correction_strategy', '')}
Original sources after rollback (copy exact blocks for old_text):
{source_context}

Correct ALL compilation and test-failure diagnostics in new_text now. In particular, qualify unresolved
class names at their point of use and fix incompatible return/assignment types.
Return the corrected edits JSON only.
"""
    raw_response = ''
    payload = {}
    response_format=context.get('implementation_response_format','edits')
    if response_format not in {'edits','complete_files'}:
        raise ValueError('Unsupported implementation response format')
    if response_format=='complete_files':
        from agents.source_generation import complete_source_prompt
        prompt=complete_source_prompt({**task,'allowed_files':allowed_files},sources,context)
    try:
        response = (provider if provider is not None else model).invoke(prompt, stage="implementation")
        raw_response = response.text
        payload = json.loads(raw_response)
        # Preserve the previously supported single-file response during migration.
        model_proposal=payload
        if response_format=='complete_files':
            from agents.source_generation import complete_source_edits
            edits=complete_source_edits(payload,sources)
            payload={'edits':edits}
        elif isinstance(payload, dict) and set(payload) == {"path", "old_text", "new_text"} and len(allowed_files) == 1:
            edits = [payload]
        elif isinstance(payload, dict) and set(payload) == {"edits"} and isinstance(payload["edits"], list):
            edits = payload["edits"]
        else:
            raise ValueError("Implementation response must contain an edits array")
        replacements = {}
        for edit in edits:
            if not isinstance(edit, dict) or set(edit) != {"path", "old_text", "new_text"}:
                raise ValueError("Each edit requires path, old_text, and new_text only")
            relative = edit["path"]
            authorized_java_target(relative, workspace, allowed_files)
            old, new = edit["old_text"], edit["new_text"]
            if not isinstance(old, str) or not isinstance(new, str) or not old or old == new:
                raise ValueError("Each edit must supply a non-empty exact replacement")
            if sources[relative].count(old) != 1:
                raise ValueError("Expected source text must occur exactly once")
            start = sources[relative].index(old)
            end = start + len(old)
            spans = replacements.setdefault(relative, [])
            if any(start < previous_end and end > previous_start for previous_start, previous_end, _ in spans):
                raise ValueError("Overlapping proposed edits in the same file")
            spans.append((start, end, new))
        if set(replacements) != set(allowed_files):
            raise ValueError("The proposal must edit every approved file")
        proposals = {}
        for relative, spans in replacements.items():
            updated = sources[relative]
            for start, end, new in sorted(spans, reverse=True):
                updated = updated[:start] + new + updated[end:]
            proposals[relative] = updated.encode("utf-8")
            if proposals[relative] == originals[relative]:
                raise ValueError("Proposed edits have no net effect on an approved file")
        if prepare_only:
            from governance.policy_engine import enforce
            enforce(proposals, originals, workspace, context.get('workflow_id', 'standalone-implementation'),
                    approvals=context.get('policy_approvals', ()))
            # Parallel workers may validate model output, but only the joined
            # coordinator is allowed to checkpoint/apply the complete set.
            return {'proposals': proposals, 'originals': originals, 'proposal': payload,
                    'model_proposal': model_proposal, 'raw_response': raw_response}
        result = apply_transaction(proposals, originals, workspace, allowed_files,
                                   context.get("workflow_id", "standalone-implementation"),
                                   policy_approvals=context.get('policy_approvals', ()))
        if context.get('plan_lineage_required'):
            from governance.plan_lineage import bind_execution
            try:result['execution_receipt']=bind_execution(context,task,result)
            except Exception:
                finish_transaction(result['transaction_id'],workspace,allowed_files,passed=False)
                raise
        return {**result, 'proposal': payload, 'model_proposal':model_proposal,
                'response_format':response_format,'raw_response': raw_response}
    except Exception as error:
        error.attempt_details = {
            'raw_response': raw_response, 'proposal': payload,
            'original_hashes': {path: file_hash(content)
                                for path, content in originals.items()},
        }
        raise
