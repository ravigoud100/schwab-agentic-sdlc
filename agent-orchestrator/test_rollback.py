import tempfile
from pathlib import Path

import governance.rollback_manager as manager


def test_rollback():
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        target = workspace / "url-shortener/src/main/java/url_shortener/ShortUrl.java"
        target.parent.mkdir(parents=True)
        target.write_text("ORIGINAL CODE")

        # Use a temporary workspace, not your real Java project.
        manager.WORKSPACE = workspace.resolve()

        checkpoint = manager.create_checkpoint(
            "url-shortener/src/main/java/url_shortener/ShortUrl.java"
        )

        target.write_text("AI GENERATED CODE")
        expected_hash = manager.file_hash(target.read_bytes())

        assert manager.rollback(checkpoint, expected_hash)
        assert target.read_text() == "ORIGINAL CODE"

        print("PASS: AI-generated changes rolled back safely.")

        # Verify that unexpected external changes block rollback.
        target.write_text("EXTERNAL CHANGE")

        try:
            manager.rollback(checkpoint, expected_hash)
        except RuntimeError:
            print("PASS: Unexpected changes protected.")
        else:
            raise AssertionError("Rollback should have been blocked")


if __name__ == "__main__":
    test_rollback()
    