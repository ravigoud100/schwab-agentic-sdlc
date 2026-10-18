"""Complete-source proposal transport; authorization stays in implement_task.

The model owns all generated Java. This adapter only uses the exact approved
original bytes as replacement boundaries, avoiding model-selected partial anchors.
"""
import json


def complete_source_prompt(task, sources, context):
    previous=context.get('previous_implementation_proposal',{})
    failed={}
    for edit in previous.get('edits',[]) if isinstance(previous,dict) else []:
        if isinstance(edit,dict) and edit.get('path') in sources:
            # New complete-file proposals have exactly the original as old_text.
            # Also materialize historical partial proposals for diagnostic replay.
            old,new=edit.get('old_text'),edit.get('new_text')
            if isinstance(old,str) and old and isinstance(new,str) and sources[edit['path']].count(old)==1:
                failed[edit['path']]=sources[edit['path']].replace(old,new,1)
    evidence=context.get('failure_context',{})
    authoritative={
        'structured_requirements':context.get('structured_requirements',{}),
        'clarification_answers':context.get('clarification_answers',{}),
        'approved_obligation_contract':context.get('obligation_contract',{}),
        'approved_design':context.get('design_artifact',{}),
        'approved_plan':context.get('plan',''),
        'plan_authorization_hash':context.get('plan_authorization',{}).get('authorization_hash'),
    }
    diagnostics = context.get('previous_validation_result', {})
    from governance.java_import_diagnostics import extract_declared_imports
    import_inventories = {path: extract_declared_imports(src) for path, src in sources.items() if path.endswith('.java')}
    import_rules = [
        "- Every referenced type MUST be resolved: either explicitly imported in that file's import header or fully qualified at the point of use.",
        "- Do NOT assume an import exists in one file just because it exists in another file in the project.",
    ]
    for path in sources:
        if path.endswith("UrlController.java") and "java.time.Instant" not in import_inventories.get(path, []):
            import_rules.append(
                f"- NOTICE: {path} does NOT currently import java.time.Instant. If your implementation uses Instant "
                "(such as Instant.now()), you MUST add `import java.time.Instant;` to that file's imports or write `java.time.Instant.now()`."
            )
        if path.endswith("UrlService.java") and "java.util.Map" not in import_inventories.get(path, []):
            import_rules.append(
                f"- NOTICE: {path} does NOT currently import java.util.Map. If your implementation uses Map "
                "(such as Map<String, Object> or Map.of), you MUST add `import java.util.Map;` to that file's imports or write `java.util.Map`."
            )
    import_rules_text = "\n".join(import_rules)
    structured_import_diag = context.get('import_diagnostics') or context.get('recovery_import_diagnostics', '')

    return f'''Implement the approved task by returning COMPLETE replacement Java files.
Return ONLY JSON: {{"files":[{{"path":"<authorized path>","content":"<complete Java source>"}}]}}.
Every authorized production path must appear exactly once. No other paths or fields.
Do not return edits, old_text, new_text, patches, insertion anchors or Markdown.
The executor replaces each entire original file ONCE. Nothing is appended to it.
Preserve package, imports, class name, all unrelated fields/methods/annotations,
existing public callers and behavior. Include the entire class and its final brace.
Do not duplicate existing methods, leave constructor statements at class scope,
or introduce extra top-level classes. Each field must be initialized correctly.
Use existing imports or fully qualified Java types/static calls consistently.
Follow the approved constructor/dependency-injection decisions EXACTLY, including
which overload Spring autowires and which constructor is for explicit test injection.
Do not assume Spring supplies dependencies that the approved design does not declare.
Existing tests and build files are read-only. Do not disable checks or weaken behavior.
Selected clarification answers and the approved contract/design override unresolved
alternatives in earlier requirements. Do not change the task, files or approved plan.

AUTHORITATIVE APPROVED CONTEXT:
{json.dumps(authoritative)}
APPROVED TASK (filtered production scope):
{json.dumps(task)}
ORIGINAL COMPLETE SOURCES, restored before this attempt:
{json.dumps(sources)}

DECLARED IMPORTS PER FILE:
{json.dumps(import_inventories, indent=2)}

TYPE RESOLUTION AND IMPORT OBLIGATIONS:
{import_rules_text}

PREVIOUS MATERIALIZED SOURCES (compiler diagnostics or architecture review refer to these, NOT the restored originals):
{json.dumps(failed)}
ACTUAL PREVIOUS MAVEN DIAGNOSTICS:
{diagnostics.get('output','')}
STRUCTURED IMPORT DIAGNOSTICS:
{structured_import_diag}
STRUCTURED FAILURE EVIDENCE:
{json.dumps(evidence)}
HUMAN-APPROVED CORRECTION STRATEGY (when supplied):
{context.get('recovery_correction_strategy','')}
Correct the failure using the ORIGINAL complete sources and approved design.
Do not copy a failed proposal unchanged. Return every complete corrected file.
Before returning, check that constructor bodies are inside constructors, methods
occur once, imports resolve, and annotations match the approved overloads.
'''


def complete_source_edits(payload,sources):
    if not isinstance(payload,dict) or set(payload)!={'files'} or not isinstance(payload['files'],list):
        raise ValueError('Complete-source response must contain only a files array')
    seen=set();edits=[]
    for item in payload['files']:
        if not isinstance(item,dict) or set(item)!={'path','content'}:
            raise ValueError('Complete-source entry requires path and content only')
        path,content=item['path'],item['content']
        if not isinstance(path,str) or path not in sources:
            raise PermissionError('Complete-source proposal targets an unauthorized file')
        if path in seen:raise ValueError('Duplicate complete-source path')
        if not isinstance(content,str) or not content.strip() or content==sources[path]:
            raise ValueError('Complete-source proposal requires a real nonempty change')
        seen.add(path)
        edits.append({'path':path,'old_text':sources[path],'new_text':content})
    if seen!=set(sources):raise ValueError('Complete-source proposal must cover every authorized production file')
    return edits
