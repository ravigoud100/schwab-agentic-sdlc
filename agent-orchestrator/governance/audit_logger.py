import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "audit.db"


def log_event(
    workflow_id: str,
    agent: str,
    event: str,
    details: dict | None = None,
):
    timestamp = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                agent TEXT NOT NULL,
                event TEXT NOT NULL,
                details TEXT
            )
        """)

        conn.execute(
            """
            INSERT INTO audit_events
            (workflow_id, timestamp, agent, event, details)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                timestamp,
                agent,
                event,
                json.dumps(details or {}),
            ),
        )

        conn.commit()
        