from pathlib import Path
import subprocess

import pytest

from governance.rollback_manager import create_checkpoint, file_hash, rollback
from governance.workspace_manager import initialize_workspace

RELATIVE_PATH = "url-shortener/src/main/java/url_shortener/ShortUrl.java"


def test_workflows_checkpoint_and_restore_independently(tmp_path):
    targets = []
    for name in ("workflow-a", "workflow-b"):
        target = tmp_path / name / RELATIVE_PATH
        target.parent.mkdir(parents=True)
        target.write_bytes(name.encode())
        targets.append(target)
    first, second = targets
    workspace = tmp_path / "workflow-a"
    checkpoint = create_checkpoint(RELATIVE_PATH, workspace)
    first.write_bytes(b"changed")
    rollback(checkpoint, file_hash(b"changed"), workspace)
    assert first.read_bytes() == b"workflow-a"
    assert second.read_bytes() == b"workflow-b"


def test_rollback_rejects_tampering_and_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / RELATIVE_PATH
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")
    checkpoint = create_checkpoint(RELATIVE_PATH, workspace)
    with pytest.raises(ValueError, match="integrity"):
        rollback({**checkpoint, "original_content": b"tampered"}, file_hash(b"original"), workspace)
    outside = tmp_path / "outside"
    outside.write_bytes(b"original")
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(PermissionError, match="escapes"):
        create_checkpoint(RELATIVE_PATH, workspace)
    with pytest.raises(PermissionError, match="escapes"):
        rollback(checkpoint, file_hash(b"original"), workspace)
    assert outside.read_bytes() == b"original"
    with pytest.raises(PermissionError):
        create_checkpoint("../outside", workspace)
    with pytest.raises(FileNotFoundError):
        create_checkpoint(RELATIVE_PATH, tmp_path / "missing")


def test_initialize_distinct_git_worktrees(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    source = tmp_path / RELATIVE_PATH
    source.parent.mkdir(parents=True)
    source.write_text("committed source")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-m", "Initial"], cwd=tmp_path, check=True, capture_output=True,
    )
    first = initialize_workspace("workflow-a", tmp_path)
    second = initialize_workspace("workflow-b", tmp_path)
    assert first != second
    (first / RELATIVE_PATH).write_text("changed")
    assert (second / RELATIVE_PATH).read_text() == "committed source"
    assert source.read_text() == "committed source"
    with pytest.raises(FileExistsError):
        initialize_workspace("workflow-a", tmp_path)
    with pytest.raises(ValueError):
        initialize_workspace("../escape", tmp_path)
