"""Policy for existing Java source edits; excludes build files and commands."""
from pathlib import Path


def authorized_java_target(relative_path: str, workspace: Path, allowed_files) -> Path:
    if not isinstance(relative_path, str) or relative_path not in allowed_files:
        raise PermissionError("File is not in the approved task allowlist")
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative_path:
        raise PermissionError("Invalid source path")
    if path.suffix != ".java" or not any(
        path.is_relative_to(prefix) for prefix in
        (Path("url-shortener/src/main/java"), Path("url-shortener/src/test/java"))
    ):
        raise PermissionError("Only existing Java source files may be modified")
    root = workspace.resolve(strict=True)
    target = (root / path).resolve(strict=True)
    if target != root / path:
        raise PermissionError("Source paths cannot use symlink aliases")
    if not target.is_relative_to(root):
        raise PermissionError("Path escapes isolated workspace")
    if not target.is_file():
        raise FileNotFoundError(target)
    return target


def authorized_new_java_target(relative_path: str, workspace: Path, allowed_new_files) -> Path:
    """Resolve one prospectively approved production Java path without creating it.

    New production files are deliberately a separate capability from edits.  The
    parent must already exist, cannot be a symlink alias, and the caller must
    present the exact path reviewed in the task plan.
    """
    if not isinstance(relative_path, str) or relative_path not in allowed_new_files:
        raise PermissionError("New file is not in the approved task allowlist")
    path = Path(relative_path)
    if (path.is_absolute() or ".." in path.parts or path.as_posix() != relative_path
            or path.suffix != ".java" or not path.is_relative_to(Path("url-shortener/src/main/java"))):
        raise PermissionError("Invalid new production source path")
    root = workspace.resolve(strict=True)
    parent = (root / path.parent).resolve(strict=True)
    if parent != root / path.parent or not parent.is_relative_to(root):
        raise PermissionError("New source parent cannot use symlink aliases")
    target = parent / path.name
    if target.exists() or target.is_symlink():
        raise FileExistsError("New production source path already exists")
    return target
