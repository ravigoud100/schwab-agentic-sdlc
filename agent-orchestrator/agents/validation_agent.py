import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

WORKSPACE = (
    PROJECT_ROOT
    / ".agent-workspaces"
    / "url-expiration"
    / "url-shortener"
).resolve()


def validate_implementation():
    print("\n[VALIDATION AGENT] Running Maven tests...")

    if not WORKSPACE.is_dir():
        raise FileNotFoundError(
            f"Workspace not found: {WORKSPACE}"
        )

    start_time = time.monotonic()

    try:
        result = subprocess.run(
            ["./mvnw", "clean", "test"],
            cwd=WORKSPACE,
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
    