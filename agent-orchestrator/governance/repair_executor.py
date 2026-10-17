from pathlib import Path

from governance.rollback_manager import create_checkpoint, file_hash


ALLOWED_REPAIR_FILES = frozenset(
    {"url-shortener/src/main/java/url_shortener/ShortUrl.java"}
)


def _authorized_target(relative_path: str, workspace: Path) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise PermissionError("Repair path must be a non-empty string")

    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PermissionError("Repair path must remain inside the isolated workspace")

    normalized = candidate.as_posix()
    if normalized not in ALLOWED_REPAIR_FILES:
        raise PermissionError("Repair target is not an authorized Java source file")

    workspace_root = workspace.resolve(strict=True)
    target = (workspace_root / normalized).resolve()
    if not target.is_relative_to(workspace_root):
        raise PermissionError("Repair path escapes the isolated workspace")
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def apply_approved_repair(change: dict, workspace: Path) -> dict:
    """Apply one human-approved, exact replacement to an allowlisted file."""
    if not isinstance(change, dict):
        raise ValueError("Approved repair must be a mapping")

    required = {"path", "old_text", "new_text"}
    if set(change) - required:
        raise ValueError("Approved repair contains unsupported fields")
    if not required.issubset(change):
        raise ValueError("Approved repair requires path, old_text, and new_text")

    relative_path = change["path"]
    old_text = change["old_text"]
    new_text = change["new_text"]
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        raise ValueError("Repair text values must be strings")
    if not old_text or old_text == new_text:
        raise ValueError("Repair must replace non-empty text with different text")

    target = _authorized_target(relative_path, workspace)
    from governance.source_policy import authorized_java_target
    target = authorized_java_target(relative_path,workspace,ALLOWED_REPAIR_FILES)
    original = target.read_bytes()
    current = original.decode('utf-8')
    if current.count(old_text) != 1:
        raise ValueError("Expected repair text must occur exactly once")

    updated=current.replace(old_text, new_text, 1)
    from governance.policy_engine import enforce, execution_context
    workflow_id,approvals=execution_context.get() or ('standalone-repair', ())
    enforce({relative_path:updated.encode('utf-8')},{relative_path:original},workspace,workflow_id,approvals=approvals)
    checkpoint = create_checkpoint(relative_path, workspace)
    if checkpoint['original_content']!=original or target.read_bytes()!=original:
        raise RuntimeError('Repair source changed before write')
    target.write_text(updated, encoding="utf-8")
    expected_current_hash = file_hash(target.read_bytes())

    return {
        "path": relative_path,
        "checkpoint": checkpoint,
        "expected_current_hash": expected_current_hash,
        "changed": True,
    }
