import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import pytest

from governance.startup_validation import (
    StartupValidationError,
    validate_python_version,
    validate_java_version,
    validate_workspace_and_maven,
    validate_live_model_availability,
    validate_feature_adapter,
    validate_environment,
)
from governance.semantic_plan import custom_expiration_contract, health_check_contract


def test_validate_python_version_success():
    ver = validate_python_version(min_version=(3, 12))
    assert ver.startswith("3.12") or ver.startswith("3.13") or ver.startswith("3.14")


def test_validate_python_version_failure(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 11, 4, "final", 0))
    with pytest.raises(StartupValidationError) as exc:
        validate_python_version(min_version=(3, 12))
    assert "Unsupported Python version" in str(exc.value)
    assert "detected 3.11.4" in str(exc.value)
    assert "Action required" in str(exc.value)


def test_validate_java_version_success():
    ver = validate_java_version(expected_major="21")
    assert "21" in ver


def test_validate_java_version_missing(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(StartupValidationError) as exc:
        validate_java_version(expected_major="21")
    assert "Java runtime not found on PATH" in str(exc.value)
    assert "Action required" in str(exc.value)


def test_validate_java_version_mismatch(monkeypatch):
    fake_proc = subprocess.CompletedProcess(
        args=["java", "-version"],
        returncode=0,
        stdout="",
        stderr='openjdk version "17.0.8" 2023-07-18\nOpenJDK 64-Bit Server VM\n',
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)
    with pytest.raises(StartupValidationError) as exc:
        validate_java_version(expected_major="21")
    assert "Unsupported Java version: detected Java 17" in str(exc.value)
    assert "Spring Boot 4.0.8 requires Java 21" in str(exc.value)


def test_validate_workspace_and_maven_success():
    info = validate_workspace_and_maven()
    assert "pom.xml" in info["pom_file"]
    assert "mvnw" in info["mvnw_file"]


def test_validate_workspace_missing_pom():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(StartupValidationError) as exc:
            validate_workspace_and_maven(workspace_path=td)
        assert "Spring Boot project POM file not found" in str(exc.value)


def test_validate_workspace_missing_mvnw():
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "url-shortener").mkdir()
        (Path(td) / "url-shortener" / "pom.xml").write_text("<project></project>")
        with pytest.raises(StartupValidationError) as exc:
            validate_workspace_and_maven(workspace_path=td)
        assert "Maven wrapper script not found" in str(exc.value)


def test_validate_workspace_mvnw_not_executable(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "url-shortener"
        ws.mkdir()
        (ws / "pom.xml").write_text("<project></project>")
        mvnw = ws / "mvnw"
        mvnw.write_text("#!/bin/sh\n")
        # Ensure not executable
        os.chmod(mvnw, 0o644)
        if sys.platform != "win32":
            with pytest.raises(StartupValidationError) as exc:
                validate_workspace_and_maven(workspace_path=td)
            assert "is not marked executable" in str(exc.value)
            assert "chmod +x" in str(exc.value)


def test_validate_workspace_invalid_maven_cache():
    with pytest.raises(StartupValidationError) as exc:
        validate_workspace_and_maven(maven_cache="/nonexistent/path/to/cache")
    assert "Specified Maven cache directory does not exist" in str(exc.value)


def test_validate_live_model_unreachable(monkeypatch):
    import urllib.request
    def fake_urlopen(*args, **kwargs):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(StartupValidationError) as exc:
        validate_live_model_availability(host="http://localhost:11434")
    assert "Ollama service is not reachable" in str(exc.value)
    assert "ollama serve" in str(exc.value)


def test_validate_live_model_missing_model(monkeypatch):
    import urllib.request
    from io import BytesIO

    class FakeResponse:
        def read(self):
            return json.dumps({"models": [{"name": "other-model:latest"}]}).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
    with pytest.raises(StartupValidationError) as exc:
        validate_live_model_availability(model_name="qwen2.5-coder:latest")
    assert "Configured primary model 'qwen2.5-coder:latest' is not installed" in str(exc.value)
    assert "ollama pull qwen2.5-coder:latest" in str(exc.value)


def test_validate_live_model_missing_fallback(monkeypatch):
    import urllib.request

    class FakeResponse:
        def read(self):
            return json.dumps({"models": [{"name": "qwen2.5-coder:latest"}]}).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
    with pytest.raises(StartupValidationError) as exc:
        validate_live_model_availability(
            model_name="qwen2.5-coder:latest",
            fallback_model="llama3.1:missing"
        )
    assert "Configured fallback model 'llama3.1:missing' is not installed" in str(exc.value)
    assert "ollama pull llama3.1:missing" in str(exc.value)


def test_validate_live_model_success(monkeypatch):
    import urllib.request

    class FakeResponse:
        def read(self):
            return json.dumps({
                "models": [
                    {"name": "qwen2.5-coder:latest"},
                    {"name": "llama3.2:3b"}
                ]
            }).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
    info = validate_live_model_availability(
        model_name="qwen2.5-coder:latest",
        fallback_model="llama3.2:3b"
    )
    assert info["primary_model"] == "qwen2.5-coder:latest"
    assert info["fallback_model"] == "llama3.2:3b"


def test_validate_feature_adapter_success():
    exp_adapter = validate_feature_adapter(contract=custom_expiration_contract())
    assert exp_adapter == "expiration"

    health_adapter = validate_feature_adapter(contract=health_check_contract())
    assert health_adapter == "health-check"

    req_adapter = validate_feature_adapter(requirement="Verify URL expiration functionality")
    assert req_adapter == "expiration"


def test_validate_feature_adapter_unsupported():
    with pytest.raises(StartupValidationError) as exc:
        validate_feature_adapter(requirement="Completely unsupported payment gateway feature")
    assert "No approved feature adapter found" in str(exc.value)
    assert "Registered feature adapters" in str(exc.value)


def test_validate_environment_offline_bypasses_ollama(monkeypatch):
    import urllib.request
    def explode(*args, **kwargs):
        raise RuntimeError("Should never attempt Ollama connection when offline")
    monkeypatch.setattr(urllib.request, "urlopen", explode)

    # With is_demo=True, Ollama check is skipped
    res = validate_environment(is_demo=True)
    assert res["status"] == "VALID"
    assert res["is_live"] is False
    assert res["is_demo"] is True

    # With LLM_PROVIDER=mock, Ollama check is skipped
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    res_mock = validate_environment(is_demo=False)
    assert res_mock["status"] == "VALID"
    assert res_mock["is_live"] is False
