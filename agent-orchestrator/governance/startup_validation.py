"""Startup validation module for the Agentic SDLC orchestrator.

Enforces strict pre-execution checks for:
- Supported Python (>= 3.12) and Java (21) versions.
- Required live model availability (Ollama daemon reachable, required models pulled).
- Valid workspace structure, POM location, and Maven wrapper permissions.
- Approved feature adapters for requested obligation contracts and requirements.
- Missing prerequisites, surfacing actionable error messages with remediation steps.

Offline fixture demonstrations (--demo ...) and mocked regression suites
(LLM_PROVIDER=mock) are explicitly permitted to run without Ollama.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error


class StartupValidationError(Exception):
    """Raised when environment prerequisites or runtime configurations fail validation."""
    pass


REPO_ROOT = Path(__file__).resolve().parents[2]


def validate_python_version(min_version: tuple[int, int] = (3, 12)) -> str:
    """Validate that the active Python interpreter meets the minimum version requirement."""
    current = sys.version_info
    major = getattr(current, "major", current[0])
    minor = getattr(current, "minor", current[1])
    micro = getattr(current, "micro", current[2] if len(current) > 2 else 0)
    detected = f"{major}.{minor}.{micro}"
    if (major, minor) < min_version:
        min_str = ".".join(map(str, min_version))
        raise StartupValidationError(
            f"Unsupported Python version: detected {detected}.\n"
            f"Python {min_str} or newer is required to run the Agentic SDLC orchestrator.\n"
            f"Action required: Create and activate a Python {min_str}+ virtual environment:\n"
            f"  python3.12 -m venv .venv\n"
            f"  source .venv/bin/activate\n"
            f"  pip install -r agent-orchestrator/requirements.txt"
        )
    return detected


def validate_java_version(expected_major: str = "21") -> str:
    """Validate that Java 21 is installed and available on PATH."""
    java_cmd = shutil.which("java")
    if not java_cmd:
        raise StartupValidationError(
            "Java runtime not found on PATH.\n"
            f"Java {expected_major} (e.g. Eclipse Temurin {expected_major}) is required to build and validate the Spring Boot application.\n"
            f"Action required: Install Java {expected_major} and ensure 'java' is on PATH, or configure JAVA_HOME."
        )

    try:
        proc = subprocess.run(["java", "-version"], capture_output=True, text=True, check=False)
        output = (proc.stderr or "") + "\n" + (proc.stdout or "")
    except Exception as exc:
        raise StartupValidationError(f"Failed to execute 'java -version': {exc}")

    match = re.search(r'version\s+"(\d+)(?:\.([\d\._]+))?"', output)
    if not match:
        match = re.search(r'openjdk\s+(\d+)', output, re.IGNORECASE)
    if not match:
        match = re.search(r'build\s+(\d+)', output, re.IGNORECASE)

    if not match:
        raise StartupValidationError(
            f"Unable to parse Java version from output:\n{output.strip()}\n"
            f"Java {expected_major} is required. Please verify your Java installation."
        )

    major_detected = match.group(1)
    if major_detected != expected_major:
        raise StartupValidationError(
            f"Unsupported Java version: detected Java {major_detected} (expected Java {expected_major}).\n"
            f"Spring Boot 4.0.8 requires Java {expected_major}.\n"
            f"Action required: Install Java {expected_major} (e.g. Eclipse Temurin 21) and configure JAVA_HOME:\n"
            f"  export JAVA_HOME=/path/to/java-{expected_major}\n"
            f"  export PATH=\"$JAVA_HOME/bin:$PATH\""
        )

    # Extract first line for report
    first_line = output.strip().splitlines()[0] if output.strip() else f"Java {major_detected}"
    return first_line


def validate_workspace_and_maven(workspace_path: Path | str | None = None,
                                 maven_cache: Path | str | None = None) -> dict:
    """Validate project layout, pom.xml, and Maven wrapper executable permissions."""
    base_dir = Path(workspace_path).resolve() if workspace_path else REPO_ROOT

    # Determine url-shortener project path
    if (base_dir / "url-shortener").is_dir():
        proj_dir = base_dir / "url-shortener"
    elif (base_dir / "pom.xml").is_file():
        proj_dir = base_dir
    else:
        proj_dir = base_dir / "url-shortener"

    pom_file = proj_dir / "pom.xml"
    if not pom_file.is_file():
        raise StartupValidationError(
            f"Spring Boot project POM file not found at '{pom_file}'.\n"
            f"Action required: Ensure the repository root or workspace contains 'url-shortener/pom.xml'."
        )

    mvnw_name = "mvnw.cmd" if sys.platform == "win32" else "mvnw"
    mvnw_file = proj_dir / mvnw_name
    if not mvnw_file.is_file():
        raise StartupValidationError(
            f"Maven wrapper script not found at '{mvnw_file}'.\n"
            f"Action required: Ensure the repository was cloned with the Maven wrapper intact."
        )

    if sys.platform != "win32" and not os.access(mvnw_file, os.X_OK):
        raise StartupValidationError(
            f"Maven wrapper '{mvnw_file}' is not marked executable.\n"
            f"Action required: Grant execution permissions:\n"
            f"  chmod +x {mvnw_file}"
        )

    cache_info = None
    if maven_cache:
        cache_path = Path(maven_cache).resolve()
        if not cache_path.is_dir():
            raise StartupValidationError(
                f"Specified Maven cache directory does not exist or is not a directory: '{cache_path}'.\n"
                f"Action required: Prepare an offline cache directory using './mvnw dependency:go-offline' "
                f"or pass a valid directory."
            )
        cache_info = str(cache_path)

    return {
        "project_dir": str(proj_dir),
        "pom_file": str(pom_file),
        "mvnw_file": str(mvnw_file),
        "maven_cache": cache_info,
    }


def validate_live_model_availability(host: str | None = None,
                                     model_name: str | None = None,
                                     fallback_model: str | None = None) -> dict:
    """Verify that the local Ollama service is reachable and required models are installed."""
    host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith("http://") and not host.startswith("https://"):
        host = f"http://{host}"
    endpoint = f"{host.rstrip('/')}/api/tags"

    try:
        req = urllib.request.Request(endpoint, headers={"User-Agent": "Schwab-Agentic-SDLC/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise StartupValidationError(
            f"Ollama service is not reachable at '{host}': {exc}.\n"
            f"Action required:\n"
            f"  1. Start the local Ollama server:\n"
            f"       ollama serve\n"
            f"  2. For offline fixture demonstrations (does NOT require Ollama):\n"
            f"       python main.py --demo greenfield\n"
            f"       python main.py --demo brownfield\n"
            f"       python main.py --demo ambiguous\n"
            f"  3. For mocked test execution:\n"
            f"       export LLM_PROVIDER=mock MOCK_SCENARIO=url-expiration"
        )

    installed_names = set()
    for item in data.get("models", []):
        name = item.get("name", "")
        if name:
            installed_names.add(name)
            if ":" in name:
                installed_names.add(name.split(":")[0])

    target_model = model_name or os.getenv("OLLAMA_MODEL", "qwen2.5-coder:latest")
    target_base = target_model.split(":")[0]

    if target_model not in installed_names and target_base not in installed_names:
        installed_list = sorted([m.get("name", "") for m in data.get("models", [])])
        raise StartupValidationError(
            f"Configured primary model '{target_model}' is not installed in local Ollama.\n"
            f"Locally available models: {installed_list or 'None'}\n"
            f"Action required: Download the model before starting a live workflow:\n"
            f"  ollama pull {target_model}"
        )

    fallback = fallback_model or os.getenv("OLLAMA_FALLBACK_MODEL")
    if fallback:
        fallback_base = fallback.split(":")[0]
        if fallback not in installed_names and fallback_base not in installed_names:
            installed_list = sorted([m.get("name", "") for m in data.get("models", [])])
            raise StartupValidationError(
                f"Configured fallback model '{fallback}' is not installed in local Ollama.\n"
                f"Locally available models: {installed_list or 'None'}\n"
                f"Action required: Download the fallback model or unset OLLAMA_FALLBACK_MODEL:\n"
                f"  ollama pull {fallback}"
            )

    return {
        "host": host,
        "primary_model": target_model,
        "fallback_model": fallback,
        "available_models": sorted(list(installed_names)),
    }


def validate_feature_adapter(contract: dict | None = None,
                             requirement: str | None = None) -> str:
    """Verify that the contract or requirement maps to an approved feature adapter."""
    from governance.feature_registry import get_feature_adapter, list_feature_adapters
    target = contract or requirement
    if not target:
        return "none"

    try:
        adapter = get_feature_adapter(target)
        return adapter.feature_id
    except Exception as exc:
        supported = [a.feature_id for a in list_feature_adapters()]
        raise StartupValidationError(
            f"No approved feature adapter found for requested feature: {exc}\n"
            f"Registered feature adapters: {supported}\n"
            f"Action required: Provide an approved obligation contract or requirement matching a registered adapter."
        )


def validate_environment(workspace_path: Path | str | None = None,
                         contract: dict | None = None,
                         requirement: str | None = None,
                         maven_cache: Path | str | None = None,
                         is_demo: bool = False,
                         is_live: bool | None = None) -> dict:
    """Comprehensive entry-gate validation before workflow execution.

    Verifies Python, Java, workspace structure, and (when live) Ollama model availability.
    Bypasses Ollama checks for offline demonstrations and mock providers.
    """
    python_ver = validate_python_version()
    java_ver = validate_java_version()
    ws_info = validate_workspace_and_maven(workspace_path=workspace_path, maven_cache=maven_cache)

    if is_live is None:
        llm_provider = os.getenv("LLM_PROVIDER", "ollama").lower()
        is_live = (llm_provider == "ollama") and not is_demo

    model_info = None
    if is_live:
        model_info = validate_live_model_availability()

    adapter_id = "none"
    if contract or requirement:
        adapter_id = validate_feature_adapter(contract=contract, requirement=requirement)

    return {
        "status": "VALID",
        "python_version": python_ver,
        "java_version": java_ver,
        "workspace": ws_info,
        "is_live": is_live,
        "is_demo": is_demo,
        "model_info": model_info,
        "feature_adapter": adapter_id,
    }
