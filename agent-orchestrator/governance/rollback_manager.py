from pathlib import Path
import hashlib

ALLOWED_FILES = {
    "url-shortener/src/main/java/url_shortener/ShortUrl.java"
}


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def create_checkpoint(relative_path: str, workspace: Path) -> dict:
    if relative_path not in ALLOWED_FILES:
        raise PermissionError("File is not authorized for rollback")

    workspace_root = workspace.resolve(strict=True)
    target = (workspace_root / relative_path).resolve()

    if not target.is_relative_to(workspace_root):
        raise PermissionError("Path escapes isolated workspace")

    if not target.is_file():
        raise FileNotFoundError(target)

    original = target.read_bytes()

    return {
        "path": relative_path,
        "original_content": original,
        "original_hash": file_hash(original),
    }


def rollback(checkpoint: dict, expected_current_hash: str, workspace: Path) -> bool:
    relative_path = checkpoint["path"]

    if relative_path not in ALLOWED_FILES:
        raise PermissionError("Unauthorized rollback target")

    workspace_root = workspace.resolve(strict=True)
    target = (workspace_root / relative_path).resolve()

    if not target.is_relative_to(workspace_root):
        raise PermissionError("Path escapes isolated workspace")

    if file_hash(checkpoint["original_content"]) != checkpoint["original_hash"]:
        raise ValueError("Checkpoint integrity verification failed")

    if file_hash(target.read_bytes()) != expected_current_hash:
        raise RuntimeError(
            "File changed unexpectedly. Refusing rollback."
        )

    target.write_bytes(checkpoint["original_content"])

    return True
