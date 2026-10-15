import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from governance import audit_logger


with tempfile.TemporaryDirectory() as directory:
    test_db = Path(directory) / "audit_test.db"

    with patch.object(audit_logger, "DB_PATH", test_db):
        audit_logger.log_event(
            workflow_id="test-workflow-001",
            agent="validation_agent",
            event="VALIDATION_FAILED",
            details={
                "retry_count": 2,
                "exit_code": 1,
            },
        )

        with sqlite3.connect(test_db) as conn:
            result = conn.execute(
                """
                SELECT workflow_id, agent, event, details
                FROM audit_events
                """
            ).fetchone()

        assert result is not None
        assert result[0] == "test-workflow-001"
        assert result[1] == "validation_agent"
        assert result[2] == "VALIDATION_FAILED"

        print("PASS: Audit event saved successfully.")
        print("Recorded event:", result)
        