import subprocess
import time
from pathlib import Path
from governance.execution_security import sanitize_execution_env

def validate_implementation(workspace: Path):
    print("\n[VALIDATION AGENT] Running Maven tests...")

    workspace_root = workspace.resolve(strict=True)
    project = (workspace_root / "url-shortener").resolve(strict=True)
    if not project.is_relative_to(workspace_root):
        raise PermissionError("Validation path escapes isolated workspace")

    if not project.is_dir():
        raise FileNotFoundError(
            f"Workspace not found: {project}"
        )

    start_time = time.monotonic()
    clean_env = sanitize_execution_env()

    try:
        result = subprocess.run(
            ["./mvnw", "clean", "test"],
            cwd=project,
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False
        )

        duration = round(time.monotonic() - start_time, 2)

        return {
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "duration_seconds": duration,
            "output": (result.stdout + "\n" + result.stderr)[-6000:]
        }

    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "exit_code": -1,
            "duration_seconds": round(
                time.monotonic() - start_time, 2
            ),
            "output": "Maven execution timed out"
        }
    