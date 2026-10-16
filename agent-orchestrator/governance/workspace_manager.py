"""Create an isolated checkout for each workflow; retain it for human review."""
from pathlib import Path
import re
import subprocess

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def initialize_workspace(workflow_id: str, project_root: Path = PROJECT_ROOT) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", workflow_id):
        raise ValueError("Invalid workflow ID")
    project_root = project_root.resolve(strict=True)
    parent = project_root / ".agent-workspaces"
    parent.mkdir(exist_ok=True)
    parent = parent.resolve(strict=True)
    if not parent.is_relative_to(project_root):
        raise PermissionError("Workspace directory escapes project root")
    workspace = parent / workflow_id
    if workspace.exists() or workspace.is_symlink():
        raise FileExistsError(workspace)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(workspace), "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return workspace.resolve(strict=True)
