"""Execution Security module for the Agentic SDLC orchestrator.

Enforces minimum necessary security controls for executing AI-generated Java code:
1. Environment Sanitization: Strips inherited credentials, API tokens, cloud keys,
   and database secrets from Maven subprocesses.
2. Workspace Containment: Strictly confines filesystem access and source edits
   within the detached isolated worktree (.agent-workspaces/).
3. Network Disabling: Enforces offline execution flags (-o) during validation
   and test execution.
4. Bounded Resource Limits: Enforces strict subprocess execution timeouts (<= 180s)
   to prevent hung processes or resource exhaustion.
"""

import os
from pathlib import Path
from typing import Mapping


SAFE_SYSTEM_VARS = frozenset({
    "PATH", "JAVA_HOME", "HOME", "USER", "LOGNAME",
    "TMPDIR", "TEMP", "TMP", "SHELL",
    "LANG", "LC_ALL", "LC_CTYPE",
    "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR",
})

SENSITIVE_PATTERNS = (
    "KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL",
    "AUTH", "PRIVATE", "SIGNING", "CERT", "DATABASE", "DB_URL",
    "AWS", "GITHUB", "OPENAI", "ANTHROPIC", "GEMINI", "OLLAMA",
)

DISALLOWED_BUILD_OPTS = (
    "MAVEN_ARGS", "MAVEN_OPTS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS",
)

MAX_PERMITTED_TIMEOUT_SECONDS = 180


def sanitize_execution_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Sanitize environment variables passed to Maven and Java subprocesses.
    
    Ensures that inherited environment secrets, API tokens, and cloud credentials
    cannot be accessed by AI-generated Java tests or build plugins via System.getenv().
    """
    source = base_env if base_env is not None else os.environ

    sanitized = {}
    for key, val in source.items():
        if key in SAFE_SYSTEM_VARS:
            sanitized[key] = val

    # Strip any variable matching sensitive patterns
    for key in list(sanitized.keys()):
        upper = key.upper()
        if any(pat in upper for pat in SENSITIVE_PATTERNS):
            sanitized.pop(key, None)

    # Proactively remove any build tool override injection vectors
    for opt in DISALLOWED_BUILD_OPTS:
        sanitized.pop(opt, None)

    return sanitized


def assert_workspace_containment(target_path: Path | str, workspace_root: Path | str) -> Path:
    """Verify that a path is strictly contained within the approved isolated workspace.
    
    Rejects directory traversal (..), absolute paths outside the workspace, and symlink escapes.
    """
    ws = Path(workspace_root).resolve(strict=False)
    target = (ws / target_path if not Path(target_path).is_absolute() else Path(target_path)).resolve(strict=False)

    try:
        if not target.is_relative_to(ws):
            raise PermissionError(
                f"Security violation: path '{target}' escapes approved workspace '{ws}'."
            )
    except AttributeError:
        # Fallback for older python or complex path comparisons
        if not str(target).startswith(str(ws)):
            raise PermissionError(
                f"Security violation: path '{target}' escapes approved workspace '{ws}'."
            )

    return target


def assert_execution_resource_limits(timeout_seconds: int | float, max_timeout: int | float = MAX_PERMITTED_TIMEOUT_SECONDS) -> None:
    """Enforce that execution timeouts are positive and bounded within security thresholds."""
    if timeout_seconds <= 0:
        raise ValueError(f"Execution timeout must be positive, got {timeout_seconds}s.")
    if timeout_seconds > max_timeout:
        raise ValueError(
            f"Execution timeout of {timeout_seconds}s exceeds maximum security threshold ({max_timeout}s)."
        )


def validate_secure_file_write(target_path: Path | str, workspace_root: Path | str,
                               allowed_files: list[str] | set[str] | None = None) -> Path:
    """Validate that a target write operation is within the workspace and permitted by the file allowlist."""
    resolved = assert_workspace_containment(target_path, workspace_root)

    if allowed_files is not None:
        ws = Path(workspace_root).resolve(strict=False)
        rel_str = str(resolved.relative_to(ws))
        norm_allowed = {str(Path(f)) for f in allowed_files}
        if rel_str not in norm_allowed:
            raise PermissionError(
                f"Security violation: target file '{rel_str}' is not in approved task allowlist: {sorted(list(norm_allowed))}."
            )

    return resolved
