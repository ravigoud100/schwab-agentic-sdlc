import json
from pathlib import Path

from llm.provider import get_model


model = get_model()


ALLOWED_REPAIR_PATH = "url-shortener/src/main/java/url_shortener/ShortUrl.java"


def parse_repair_proposal(proposal: str) -> dict | None:
    """Extract only a strict, executable change from a model proposal."""
    if not isinstance(proposal, str):
        return None

    try:
        candidate = json.loads(proposal)
    except json.JSONDecodeError:
        return None

    if not isinstance(candidate, dict):
        return None
    if set(candidate) - {"path", "old_text", "new_text", "reason"}:
        return None
    if candidate.get("path") != ALLOWED_REPAIR_PATH:
        return None
    if not isinstance(candidate.get("old_text"), str):
        return None
    if not isinstance(candidate.get("new_text"), str):
        return None
    if not candidate["old_text"] or candidate["old_text"] == candidate["new_text"]:
        return None

    return {
        "path": candidate["path"],
        "old_text": candidate["old_text"],
        "new_text": candidate["new_text"],
    }


def propose_repair(error_log: str, workspace: Path) -> str:
    print("\n[REPAIR AGENT] Analyzing validation failure...")

    if not error_log.strip():
        raise ValueError("Validation error log cannot be empty")

    workspace_root = workspace.resolve(strict=True)
    java_file = (workspace_root / ALLOWED_REPAIR_PATH).resolve()
    if not java_file.is_relative_to(workspace_root):
        raise PermissionError("Repair source escapes isolated workspace")

    if not java_file.is_file():
        raise FileNotFoundError(java_file)

    actual_code = java_file.read_text(encoding="utf-8")
    prompt = f"""You are a senior Java 21 Spring Boot engineer.

Analyze the Maven error against the actual Java source code.

Additional safety rules:
9. Never invent a root cause without supporting evidence.
10. If the reported missing method already exists in the source, explain the contradiction.
11. Do not propose changes when the evidence is insufficient.
12. Consider stale compilation output, mismatched source versions, or incorrect build configuration when errors contradict the code.
13. Never recommend disabling tests or bypassing validation.
14. If a source edit is justified, return only a JSON object with exactly these
    fields: path, old_text, new_text, and reason. The path must be the exact
    allowlisted Java path. Do not return commands, patches, or other file paths.
15. If a source edit is not justified, return an evidence-based explanation and
    do not return an executable JSON object.

MAVEN ERROR:
{error_log[-4000:]}

ACTUAL JAVA SOURCE:
{actual_code}

Propose a minimal repair. Do not modify files or execute commands."""

    response = model.invoke(prompt, stage="repair")
    return response.text
