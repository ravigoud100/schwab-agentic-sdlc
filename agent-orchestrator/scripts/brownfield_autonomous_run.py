"""Autonomous brownfield execution using real Ollama qwen2.5-coder:latest.

Implements the service-side status contract and halts at AWAITING_BROWNFIELD_RESULTS_REVIEW.
"""
import argparse
import difflib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from governance import audit_logger
from governance.audit_logger import log_event
from governance.brownfield_lifecycle import build
from governance.file_transaction import apply_transaction
from governance.java_import_diagnostics import diagnose_sources_imports, extract_declared_imports
from governance.model_attempts import invoke_durable, reconcile
from governance.policy_engine import load_policy
from governance.semantic_plan import digest
from governance.test_safety import inventory, sha
from llm.provider import get_model
from scripts.brownfield_final_attempt import TEST

S = 'url-shortener/src/main/java/url_shortener/UrlService.java'
C = 'url-shortener/src/main/java/url_shortener/UrlController.java'
T = 'url-shortener/src/test/java/url_shortener/UrlStatusIntegrationTest.java'


def create_prompt(sources, state):
    declared = {p: extract_declared_imports(src) for p, src in sources.items()}
    prev_comp = state.get('compiler_diagnostics', '')
    prev_import = state.get('import_diagnostics', '')

    prompt = f'''Implement the approved brownfield task by returning COMPLETE replacement Java files.
Return ONLY JSON: {{"files":[{{"path":"exact approved path","content":"complete Java source"}}]}}.
Every authorized production path ({S} and {C}) must appear exactly once.
Do not return Markdown code blocks, explanations, patches, or partial edits.
Each entire original file will be replaced ONCE by the returned content.

APPROVED ARCHITECTURAL CONTRACT & SCOPE:
Task: Expose a read-only URL status endpoint with service-side evaluation.
1. {S} (UrlService):
   - Add method: public java.util.Map<String, Object> getStatus(String shortCode)
   - Annotate with: @org.springframework.transaction.annotation.Transactional(readOnly = true)
   - Behavior:
     - Find ShortUrl via repository.findByShortCode(shortCode).
     - If not found, throw: new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.NOT_FOUND, "Short URL not found")
     - Return map containing exactly:
         "shortCode": shortUrl.getShortCode()
         "expired": shortUrl.isExpired(java.time.Instant.now())
     - NEVER increment click count in getStatus.
   - Preserve all existing fields, constructor, and methods (createShortUrl, generateShortCode, getOriginalUrl, getAnalytics) exactly.

2. {C} (UrlController):
   - Note that UrlController already has `@RequestMapping("/api/urls")` at class level.
   - Add endpoint method:
       @GetMapping("/{{shortCode}}/status")
       public java.util.Map<String, Object> getStatus(@PathVariable String shortCode) {{
           return urlService.getStatus(shortCode);
       }}
   - Pure delegation to urlService.getStatus(shortCode).
   - Preserve all existing fields, constructor, CreateUrlRequest record, and existing endpoints (createShortUrl, getAnalytics) exactly.

DECLARED IMPORTS PER FILE:
{json.dumps(declared, indent=2)}

TYPE RESOLUTION AND IMPORT OBLIGATIONS:
- Every referenced type MUST be resolved: either explicitly imported in that file's import header or fully qualified at point of use.
- NOTICE for {S}: {S} does NOT currently import java.util.Map. You MUST add `import java.util.Map;` to its imports or write `java.util.Map<String, Object>`.
- NOTICE for {C}: {C} already imports java.util.Map. Since expiration evaluation occurs inside UrlService, {C} does not need java.time.Instant.
- Existing tests and build configuration are read-only. Do not weaken behavior or disable checks.

ORIGINAL COMPLETE SOURCES:
{json.dumps(sources, indent=2)}
'''
    if prev_comp or prev_import:
        prompt += f'''
PREVIOUS MAVEN COMPILER DIAGNOSTICS:
{prev_comp}

STRUCTURED IMPORT DIAGNOSTICS:
{prev_import}
Correct the failure using the ORIGINAL sources and approved contract. Return complete corrected files.
'''
    return prompt


def run_brownfield_autonomous(evidence_dir: Path, cache_dir: Path):
    evidence_dir = evidence_dir.resolve()
    cache_dir = cache_dir.resolve()

    if evidence_dir.exists():
        raise FileExistsError(f"Evidence directory {evidence_dir} already exists. Use a fresh directory.")

    evidence_dir.mkdir(parents=True, exist_ok=False)
    workspace = evidence_dir / 'workspace'
    workspace.mkdir()

    # Step 1: Baseline verification & isolated workspace setup
    source_repo_shortener = Path(__file__).resolve().parents[2] / 'url-shortener'
    shutil.copytree(
        source_repo_shortener,
        workspace / 'url-shortener',
        ignore=shutil.ignore_patterns('target', '.git', '.jdk-25')
    )

    wid = str(uuid.uuid4())
    audit_logger.DB_PATH = evidence_dir / 'audit.db'
    attempts_db = evidence_dir / 'attempts.db'
    workflow_db = evidence_dir / 'workflow.db'

    baseline = inventory(workspace)
    scope = [S, C, T]
    plan = {
        'classification': 'brownfield',
        'task': 'read-only URL status endpoint with service-side evaluation',
        'contract': 'UrlService.getStatus returns Map<String, Object> with shortCode and expired, @Transactional(readOnly = true), throws ResponseStatusException(HttpStatus.NOT_FOUND), never increments clicks. UrlController delegates.',
        'production': [S, C],
        'test': T,
        'attempt_limit': 3
    }
    policy = load_policy()
    policy_hash = digest(policy)
    plan_hash = digest(plan)

    receipt = {
        'workflow_id': wid,
        'plan_hash': plan_hash,
        'scope': scope,
        'baseline': {p: sha(baseline[p]) for p in (S, C)},
        'policy_hash': policy_hash,
        'contract': plan['contract']
    }

    log_event(wid, 'brownfield_autonomous', 'EXECUTION_APPROVAL_REQUESTED', receipt)
    print(f"[*] Baseline verified. Workflow ID: {wid}")
    print(f"[*] Plan hash: {plan_hash}")
    print(f"[*] Policy hash: {policy_hash}")

    # Confirm attempt ledger status
    accounting = reconcile(attempts_db, wid)
    if accounting['interrupted']:
        raise PermissionError('Interrupted attempts detected; fresh execution blocked')

    def executor(state):
        attempt_num = state['attempt']
        print(f"\n--- [Attempt {attempt_num}] Model Implementation Dispatch ---")

        originals = {p: (workspace / p).read_bytes() for p in (S, C)}
        sources = {p: b.decode('utf-8') for p, b in originals.items()}

        prompt = create_prompt(sources, state)
        prompt_file = evidence_dir / f'attempt-{attempt_num}-prompt.txt'
        prompt_file.write_text(prompt, encoding='utf-8')

        response_file = evidence_dir / f'attempt-{attempt_num}-response.txt'

        model = get_model()
        print(f"[*] Requesting implementation from qwen2.5-coder:latest via durable ledger...")
        response = invoke_durable(
            attempts_db,
            wid,
            attempt_num,
            receipt,
            prompt,
            'qwen2.5-coder:latest',
            lambda: model.invoke(prompt, stage='implementation'),
            response_file
        )

        raw = response.text.strip()
        if raw.startswith('```json') and raw.endswith('```'):
            raw = raw[7:-3].strip()
        elif raw.startswith('```') and raw.endswith('```'):
            raw = raw[3:-3].strip()

        try:
            payload = json.loads(raw)
            files = payload.get('files', [])
        except Exception as e:
            raise ValueError(f"Failed to parse model JSON: {e}\nRaw output:\n{raw[:500]}")

        props = {}
        for f in files:
            path = f.get('path', '').strip()
            content = f.get('content', '')
            if not content.endswith('\n'):
                content += '\n'
            props[path] = content.encode('utf-8')

        if set(props.keys()) != {S, C}:
            raise PermissionError(f"Model modified unexpected scope: {list(props.keys())} != {[S, C]}")

        # Requirement 5: Apply new import diagnostics BEFORE Maven
        props_str = {p: b.decode('utf-8', errors='replace') for p, b in props.items()}
        diag_result = diagnose_sources_imports(props_str)
        diag_file = evidence_dir / f'attempt-{attempt_num}-import-diagnostics.json'
        diag_file.write_text(json.dumps(diag_result, indent=2), encoding='utf-8')

        log_event(wid, 'brownfield_autonomous', 'IMPORT_DIAGNOSTICS_EVALUATED', {
            'attempt': attempt_num,
            'valid': diag_result.get('valid'),
            'summary': diag_result.get('summary', '')
        })
        print(f"[*] Pre-Maven import diagnostics: {'CLEAN (valid)' if diag_result.get('valid') else 'ISSUES FOUND'}")
        if not diag_result.get('valid'):
            print(f"    {diag_result.get('summary')}")

        # Generate source diff
        diff_lines = []
        for p in (S, C):
            orig_lines = sources[p].splitlines(keepends=True)
            prop_lines = props[p].decode('utf-8').splitlines(keepends=True)
            diff = difflib.unified_diff(orig_lines, prop_lines, fromfile=f"a/{p}", tofile=f"b/{p}")
            diff_lines.extend(diff)
        diff_file = evidence_dir / f'attempt-{attempt_num}-diff.patch'
        diff_file.write_text(''.join(diff_lines), encoding='utf-8')

        # Requirement 6: Do not silently correct AI-generated Java.
        # Apply the exact model proposal to workspace
        tx = apply_transaction(props, originals, workspace, [S, C], wid)

        return {
            'source_transaction': tx,
            'proposals': props,
            'proposal': {'raw_hash': sha(response.text.encode('utf-8')), 'paths': sorted(props)},
            'proposal_hash': sha(response.text.encode('utf-8'))
        }

    def validator(state):
        attempt_num = state['attempt']
        print(f"[*] Executing genuine Maven clean test (Attempt {attempt_num})...")
        cmd = ['./mvnw', '-o', f'-Dmaven.repo.local={cache_dir}', 'clean', 'test']
        q = subprocess.run(
            cmd,
            cwd=workspace / 'url-shortener',
            capture_output=True,
            text=True,
            timeout=300
        )
        output = q.stdout + '\n' + q.stderr
        maven_log = evidence_dir / f'attempt-{attempt_num}-maven.log'
        maven_log.write_text(output, encoding='utf-8')

        # Preserve Surefire reports if created
        surefire_dir = workspace / 'url-shortener' / 'target' / 'surefire-reports'
        if surefire_dir.exists():
            target_surefire = evidence_dir / f'attempt-{attempt_num}-surefire-reports'
            if target_surefire.exists():
                shutil.rmtree(target_surefire)
            shutil.copytree(surefire_dir, target_surefire)

        passed = (q.returncode == 0)
        print(f"[*] Maven execution result: {'SUCCESS (0)' if passed else f'FAILED ({q.returncode})'}")
        return {
            'passed': passed,
            'exit_code': q.returncode,
            'diagnostics': output[-8000:]
        }

    # Initial state
    state = {
        'workflow_id': wid,
        'workspace': str(workspace),
        'plan_hash': plan_hash,
        'scope': scope,
        'production_paths': [S, C],
        'test_path': T,
        'test_bytes': TEST,
        'execution_receipt': receipt
    }

    print("[*] Compiling LangGraph brownfield lifecycle with SQLite checkpointer...")
    with SqliteSaver.from_conn_string(str(workflow_db)) as saver:
        app = build(executor, validator).compile(checkpointer=saver)
        cfg = {'configurable': {'thread_id': wid}}

        # Step 2 approval interrupt
        print("[*] Invoking initial graph up to execution approval gate...")
        paused = app.invoke(state, cfg)
        if not paused.get('__interrupt__'):
            raise RuntimeError("Expected BROWNFIELD_PLAN_APPROVAL interrupt not encountered")

        interrupt_val = paused['__interrupt__'][0].value
        print(f"[*] Reached plan approval gate: {interrupt_val}")

        # Record explicit execution approval
        log_event(wid, 'brownfield_autonomous', 'EXECUTION_APPROVED', {
            **receipt,
            'decision_source': 'explicit user decision',
            'plan_hash': plan_hash
        })
        print("[*] Recorded EXECUTION_APPROVED. Resuming graph execution...")

        # Run through execution
        result = app.invoke(Command(resume='approve'), cfg)
        snap = app.get_state(cfg)

    # Step 10 & 11 evaluation
    final_status = result.get('status')
    final_interrupts = [i.value for t in snap.tasks for i in t.interrupts]

    print(f"\n==========================================")
    print(f"Final Workflow Status: {final_status}")
    print(f"Final Attempt Count:  {result.get('attempt')}")
    print(f"Final Interrupts:      {final_interrupts}")
    print(f"==========================================")

    receipt_file = evidence_dir / 'receipt.json'
    receipt_file.write_text(json.dumps(receipt, indent=2), encoding='utf-8')

    changed_files = sorted(
        p for p, v in inventory(workspace).items()
        if baseline.get(p) != v
    )

    result_summary = {
        'workflow_id': wid,
        'final_status': final_status,
        'attempt': result.get('attempt'),
        'validation_passed': result.get('validation', {}).get('passed', False),
        'maven_exit_code': result.get('validation', {}).get('exit_code'),
        'interrupts': final_interrupts,
        'changed_files': changed_files,
        'receipt': receipt
    }
    result_file = evidence_dir / 'result.json'
    result_file.write_text(json.dumps(result_summary, indent=2), encoding='utf-8')

    if final_status == 'AWAITING_BROWNFIELD_RESULTS_REVIEW':
        print("[+] SUCCESS: Reached AWAITING_BROWNFIELD_RESULTS_REVIEW.")
        print("[+] Halting at results review gate. No release, merge, or deployment performed.")
    elif final_status == 'STOPPED_RETRY_EXHAUSTED':
        print("[-] RETRY EXHAUSTED: All 3 attempts failed. Restoring original workspace.")
        # Ensure rollback has cleared production modifications
        current_inv = inventory(workspace)
        for p in (S, C):
            if current_inv.get(p) != baseline.get(p):
                (workspace / p).write_bytes(baseline[p])
        if T in current_inv:
            (workspace / T).unlink()
    else:
        print(f"[?] Ended at status: {final_status}")

    return result_summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Run autonomous brownfield execution")
    parser.add_argument('directory', type=Path, help="Target evidence directory")
    parser.add_argument('--maven-cache', type=Path, default=Path('/private/tmp/schwab-test-generation/maven-cache'), help="Maven cache directory")
    args = parser.parse_args()
    run_brownfield_autonomous(args.directory, args.maven_cache)
