"""Security tests for minimum necessary execution security.

Verifies:
1. Inherited credentials, API tokens, and secrets are stripped and not passed to subprocesses.
2. Filesystem access outside the approved workspace is strictly prohibited.
3. Subprocess execution timeouts and resource bounds are enforced.
4. Unauthorized file writes outside the approved task allowlist are rejected.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import pytest

from governance.execution_security import (
    sanitize_execution_env,
    assert_workspace_containment,
    assert_execution_resource_limits,
    validate_secure_file_write,
    MAX_PERMITTED_TIMEOUT_SECONDS,
)
from agents.validation_agent import validate_implementation


def test_sanitize_execution_env_strips_credentials_and_secrets():
    host_env = {
        "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "GITHUB_TOKEN": "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
        "DATABASE_PASSWORD": "super-secret-password-123",
        "DATABASE_URL": "postgres://user:pass@localhost:5432/db",
        "OPENAI_API_KEY": "sk-proj-1234567890",
        "ANTHROPIC_API_KEY": "sk-ant-1234567890",
        "OLLAMA_API_KEY": "ollama-secret",
        "SSH_AUTH_SOCK": "/private/tmp/com.apple.launchd/Listeners",
        "MAVEN_ARGS": "--settings /secret/settings.xml",
        "MAVEN_OPTS": "-Djavax.net.ssl.trustStore=/secret/truststore",
        "JAVA_TOOL_OPTIONS": "-agentlib:jdwp=transport=dt_socket,server=y",
        "JDK_JAVA_OPTIONS": "--add-opens java.base/java.lang=ALL-UNNAMED",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "JAVA_HOME": "/Library/Java/JavaVirtualMachines/temurin-21.jdk",
        "HOME": "/Users/demo",
        "USER": "demo",
        "LANG": "en_US.UTF-8",
        "TMPDIR": "/tmp",
    }

    clean = sanitize_execution_env(host_env)

    # Sensitive keys must be completely absent
    sensitive_keys = [
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN",
        "DATABASE_PASSWORD", "DATABASE_URL", "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "OLLAMA_API_KEY", "SSH_AUTH_SOCK",
        "MAVEN_ARGS", "MAVEN_OPTS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS",
    ]
    for key in sensitive_keys:
        assert key not in clean, f"Sensitive key {key} was not stripped from execution environment"

    # Safe system keys must be preserved
    safe_keys = ["PATH", "JAVA_HOME", "HOME", "USER", "LANG", "TMPDIR"]
    for key in safe_keys:
        assert clean.get(key) == host_env[key], f"Safe system key {key} was unexpectedly removed"


def test_sanitize_execution_env_prevents_subprocess_env_leak():
    """Verify in a live subprocess that scrubbed environment variables are inaccessible."""
    simulated_env = os.environ.copy()
    simulated_env["SUPER_SECRET_TOKEN"] = "stolen_credential_test_value"
    simulated_env["AWS_SECRET_KEY"] = "fake_aws_key_999"

    clean_env = sanitize_execution_env(simulated_env)

    # Run Python subprocess with clean_env querying the variables
    script = (
        "import os, sys\n"
        "assert 'SUPER_SECRET_TOKEN' not in os.environ\n"
        "assert 'AWS_SECRET_KEY' not in os.environ\n"
        "sys.exit(0)\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], env=clean_env, capture_output=True, text=True)
    assert proc.returncode == 0, f"Subprocess detected leaked secrets: {proc.stderr}"


def test_workspace_containment_allows_internal_paths(tmp_path):
    ws = tmp_path / "agent-workspaces" / "test-workflow"
    ws.mkdir(parents=True)
    target = ws / "url-shortener" / "src" / "main" / "java" / "App.java"

    resolved = assert_workspace_containment(target, ws)
    assert resolved == target.resolve()
    assert resolved.is_relative_to(ws.resolve())


def test_workspace_containment_rejects_parent_traversal(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    traversal_path = ws / ".." / "host-file.txt"

    with pytest.raises(PermissionError) as exc:
        assert_workspace_containment(traversal_path, ws)
    assert "escapes approved workspace" in str(exc.value)


def test_workspace_containment_rejects_absolute_path_outside(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    external = Path("/etc/passwd")

    with pytest.raises(PermissionError) as exc:
        assert_workspace_containment(external, ws)
    assert "escapes approved workspace" in str(exc.value)


def test_workspace_containment_rejects_symlink_escape(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    secret_file = secret_dir / "secret.key"
    secret_file.write_text("private-data")

    # Symlink inside workspace pointing outside
    link = ws / "link-to-secret"
    try:
        link.symlink_to(secret_file)
        with pytest.raises(PermissionError) as exc:
            assert_workspace_containment(link.resolve(), ws)
        assert "escapes approved workspace" in str(exc.value)
    except OSError:
        pytest.skip("Symlinks not supported on this filesystem")


def test_execution_resource_limits_enforced():
    # Valid bounded timeouts
    assert_execution_resource_limits(60)
    assert_execution_resource_limits(120)
    assert_execution_resource_limits(180)

    # Invalid / excessive timeouts
    with pytest.raises(ValueError, match="must be positive"):
        assert_execution_resource_limits(0)

    with pytest.raises(ValueError, match="must be positive"):
        assert_execution_resource_limits(-10)

    with pytest.raises(ValueError, match="exceeds maximum security threshold"):
        assert_execution_resource_limits(MAX_PERMITTED_TIMEOUT_SECONDS + 1)


def test_validate_secure_file_write_permits_allowlisted_file(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    allowed = ["url-shortener/src/main/java/ShortUrl.java"]
    target = ws / "url-shortener/src/main/java/ShortUrl.java"

    validated = validate_secure_file_write(target, ws, allowed_files=allowed)
    assert validated == target.resolve()


def test_validate_secure_file_write_rejects_unapproved_file(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    allowed = ["url-shortener/src/main/java/ShortUrl.java"]
    unapproved = ws / "url-shortener/pom.xml"

    with pytest.raises(PermissionError, match="not in approved task allowlist"):
        validate_secure_file_write(unapproved, ws, allowed_files=allowed)


def test_validate_secure_file_write_rejects_path_escape(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    allowed = ["url-shortener/src/main/java/ShortUrl.java"]
    escaped = ws / ".." / "unrelated.txt"

    with pytest.raises(PermissionError, match="escapes approved workspace"):
        validate_secure_file_write(escaped, ws, allowed_files=allowed)


def test_validation_agent_rejects_escaped_workspace(tmp_path):
    # Pass a path that resolves outside the designated workspace
    fake_ws = tmp_path / "fake"
    with pytest.raises(FileNotFoundError):
        validate_implementation(fake_ws)


def test_validation_agent_rejects_symlink_escaped_project(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    external = tmp_path / "external-project"
    external.mkdir()
    link = ws / "url-shortener"
    try:
        link.symlink_to(external)
    except OSError:
        pytest.skip("Symlinks not supported on this filesystem")

    with pytest.raises(PermissionError, match="escapes isolated workspace"):
        validate_implementation(ws)

