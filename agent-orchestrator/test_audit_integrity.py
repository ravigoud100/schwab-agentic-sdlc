"""Regression tests for tamper-evident audit logging, chain verification, and concurrency."""
import hashlib
import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from governance import audit_logger
from governance.audit_logger import (
    GENESIS_HASH,
    ensure_schema,
    log_event,
    seal_legacy_baseline,
    verify_audit_integrity,
    verify_audit_integrity_readonly,
)
from governance.documentation_release import evaluate


def test_legacy_migration_preserves_existing_event_fields_exactly(tmp_path):
    """Legacy baseline sealing must preserve every event payload, ID, timestamp, and workflow ID bit-for-bit."""
    db_path = tmp_path / "legacy.db"

    # Create a legacy database without prev_hash or row_hash
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                agent TEXT NOT NULL,
                event TEXT NOT NULL,
                details TEXT
            )
            """
        )
        sample_records = [
            (1, "wf-100", "2026-10-18T10:00:00+00:00", "orchestrator", "WORKFLOW_STARTED", json.dumps({"mode": "sequential"})),
            (2, "wf-100", "2026-10-18T10:01:00+00:00", "task_planner", "PLAN_AUTHORIZED", json.dumps({"plan_hash": "abc123hash", "tasks": 3})),
            (3, "wf-100", "2026-10-18T10:02:00+00:00", "validation_agent", "VALIDATION_PASSED", json.dumps({"exit_code": 0, "cases": {"test_1": "passed"}})),
        ]
        conn.executemany(
            "INSERT INTO audit_events (id, workflow_id, timestamp, agent, event, details) VALUES (?, ?, ?, ?, ?, ?)",
            sample_records,
        )

    # Capture exact original records before migration
    with sqlite3.connect(db_path) as conn:
        before_rows = conn.execute(
            "SELECT id, workflow_id, timestamp, agent, event, details FROM audit_events ORDER BY id"
        ).fetchall()

    # Perform legacy baseline sealing
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        sealed_count = seal_legacy_baseline(conn)

    assert sealed_count == 3

    # Verify that original 6 fields are completely unchanged
    with sqlite3.connect(db_path) as conn:
        after_rows = conn.execute(
            "SELECT id, workflow_id, timestamp, agent, event, details FROM audit_events ORDER BY id"
        ).fetchall()

    assert before_rows == after_rows

    # Verify that newly sealed chain is valid
    valid, report = verify_audit_integrity(db_path)
    assert valid is True
    assert report["total_records"] == 3


def test_concurrent_audit_writes_maintain_one_valid_global_chain(tmp_path):
    """Concurrent threads writing to audit logger must produce a single unbroken valid chain without lock errors."""
    db_path = tmp_path / "concurrent_audit.db"

    with patch.object(audit_logger, "DB_PATH", db_path):
        num_threads = 10
        events_per_thread = 20
        errors = []

        def worker(thread_idx):
            try:
                for ev_idx in range(events_per_thread):
                    log_event(
                        workflow_id=f"concurrent-wf-{thread_idx}",
                        agent=f"worker-{thread_idx}",
                        event="CONCURRENT_EVENT",
                        details={"thread": thread_idx, "index": ev_idx},
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Encountered concurrency errors: {errors}"

        # Verify that all 200 events are present and form a single unbroken chain
        valid, report = verify_audit_integrity(db_path)
        assert valid is True, f"Audit chain verification failed: {report}"
        assert report["total_records"] == num_threads * events_per_thread
        assert report["head_id"] == num_threads * events_per_thread


def test_modified_payloads_are_detected(tmp_path):
    """Modifying any payload field (event, details, timestamp, agent) must trigger PAYLOAD_MODIFIED."""
    db_path = tmp_path / "tampered.db"

    with patch.object(audit_logger, "DB_PATH", db_path):
        log_event("wf-1", "agent-1", "EVENT_1", {"key": "val1"})
        log_event("wf-1", "agent-1", "EVENT_2", {"key": "val2"})
        log_event("wf-1", "agent-1", "EVENT_3", {"key": "val3"})

    valid, _ = verify_audit_integrity(db_path)
    assert valid is True

    # Tamper with row 2's details
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE audit_events SET details = ? WHERE id = 2", (json.dumps({"key": "FORGED"}),))

    valid, report = verify_audit_integrity(db_path)
    assert valid is False
    assert report["error"] == "PAYLOAD_MODIFIED"
    assert report["record_id"] == 2


def test_deleted_intermediate_events_are_detected(tmp_path):
    """Deleting an intermediate event must trigger MISSING_INTERMEDIATE_ID / broken chain."""
    db_path = tmp_path / "deletion.db"

    with patch.object(audit_logger, "DB_PATH", db_path):
        log_event("wf-1", "agent-1", "EVENT_1")
        log_event("wf-1", "agent-1", "EVENT_2")
        log_event("wf-1", "agent-1", "EVENT_3")

    # Delete row 2
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM audit_events WHERE id = 2")

    valid, report = verify_audit_integrity(db_path)
    assert valid is False
    assert report["error"] == "MISSING_INTERMEDIATE_ID"
    assert report["record_id"] == 3
    assert report["expected_id"] == 2


def test_invalid_genesis_values_are_detected(tmp_path):
    """Invalid genesis prev_hash or non-1 genesis ID must be detected."""
    db_path = tmp_path / "invalid_genesis.db"

    with patch.object(audit_logger, "DB_PATH", db_path):
        log_event("wf-1", "agent-1", "EVENT_1")

    # Tamper with genesis prev_hash
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE audit_events SET prev_hash = ? WHERE id = 1", ("1" * 64,))

    valid, report = verify_audit_integrity(db_path)
    assert valid is False
    assert report["error"] == "INVALID_GENESIS_HASH"
    assert report["record_id"] == 1

    # Tamper with genesis id
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE audit_events SET prev_hash = ?, id = 2 WHERE id = 1", (GENESIS_HASH,))

    valid, report = verify_audit_integrity(db_path)
    assert valid is False
    assert report["error"] == "INVALID_GENESIS_ID"
    assert report["record_id"] == 2


def test_workflow_specific_verification_cannot_hide_global_corruption(tmp_path):
    """Workflow-specific verification must fail if any record in the global chain is corrupted."""
    db_path = tmp_path / "multi_wf.db"

    with patch.object(audit_logger, "DB_PATH", db_path):
        log_event("wf-clean", "agent-1", "EVENT_1")
        log_event("wf-dirty", "agent-2", "EVENT_2")
        log_event("wf-clean", "agent-1", "EVENT_3")

    # Verify both workflows initially
    valid, report = verify_audit_integrity(db_path, workflow_id="wf-clean")
    assert valid is True
    assert report["workflow_records"] == 2

    # Corrupt wf-dirty
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE audit_events SET agent = 'evil_agent' WHERE id = 2")

    # Now verify wf-clean - it MUST still fail because global chain is corrupted!
    valid, report = verify_audit_integrity(db_path, workflow_id="wf-clean")
    assert valid is False
    assert report["error"] == "PAYLOAD_MODIFIED"
    assert report["record_id"] == 2
    assert report["workflow_id"] == "wf-dirty"


def test_unsealed_legacy_database_fails_read_only_verification_without_modification(tmp_path):
    """Read-only verification of unsealed legacy schema must fail and leave file unmodified."""
    db_path = tmp_path / "unsealed.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE audit_events (id INTEGER PRIMARY KEY, workflow_id TEXT, timestamp TEXT, agent TEXT, event TEXT, details TEXT)")
        conn.execute("INSERT INTO audit_events VALUES (1, 'wf-1', '2026-10-18T12:00:00Z', 'agent', 'EV', '{}')")

    initial_bytes = db_path.read_bytes()
    initial_sha = hashlib.sha256(initial_bytes).hexdigest()

    valid, report = verify_audit_integrity_readonly(db_path)
    assert valid is False
    assert report["error"] == "UNSEALED_LEGACY_SCHEMA"

    # Verify file is bit-for-bit identical (zero modifications)
    after_bytes = db_path.read_bytes()
    after_sha = hashlib.sha256(after_bytes).hexdigest()
    assert initial_sha == after_sha


def test_audit_corruption_blocks_release_readiness(tmp_path):
    """Any audit log corruption must block release readiness and record blocker."""
    db_path = tmp_path / "readiness_audit.db"
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    with patch.object(audit_logger, "DB_PATH", db_path):
        log_event("run-001", "orchestrator", "WORKFLOW_STARTED")
        log_event("run-001", "reviewer", "TEST_RESULTS_APPROVED", {"bundle_hash": "bundle-xyz"})
        log_event("run-001", "documenter", "DOCUMENTATION_GATE_PASSED", {"sha256": "doc-hash", "bundle_hash": "bundle-xyz"})

    doc_file = out_dir / "engineering-notes.md"
    doc_content = "# Verified engineering documentation\n\n## Implementation Notes\n\nNote 1\n\n"
    doc_file.write_text(doc_content)
    doc_sha = hashlib.sha256(doc_content.encode()).hexdigest()

    state = {
        "run_id": "run-001",
        "bundle_hash": "bundle-xyz",
        "output_dir": str(out_dir),
        "test_review_hash": "bundle-xyz",
        "document": {
            "path": str(doc_file),
            "sha256": doc_sha,
            "bundle_hash": "bundle-xyz",
            "selection": {"implementation_notes": [0]},
        },
    }

    mock_manifest = {
        "workspace": str(tmp_path / "workspace"),
        "profile": "integrated-expiration-v1",
        "approved_implementation_plan_hash": "plan-123",
        "implementation_hash": "src-123",
        "generated_test_hash": "gen-123",
        "records": {"baseline": {"cases": {"test": "passed"}}},
    }

    # When audit is intact, audit_log_integrity_verified is True
    with patch.object(audit_logger, "DB_PATH", db_path), \
         patch("governance.documentation_release.current", return_value=mock_manifest), \
         patch("governance.documentation_release.facts", return_value={"implementation_notes": ["Note 1"]}):
        result_clean = evaluate(state)
        assert result_clean["readiness"]["checks"]["audit_log_integrity_verified"] is True

    # Now corrupt audit log
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE audit_events SET event = 'TAMPERED_EVENT' WHERE id = 1")

    # Release readiness evaluation must block and flag audit_log_integrity_verified = False
    with patch.object(audit_logger, "DB_PATH", db_path), \
         patch("governance.documentation_release.current", return_value=mock_manifest), \
         patch("governance.documentation_release.facts", return_value={"implementation_notes": ["Note 1"]}):
        result_corrupted = evaluate(state)
        assert result_corrupted["readiness"]["checks"]["audit_log_integrity_verified"] is False
        assert result_corrupted["readiness"]["status"] == "BLOCKED"
        assert "audit_log_integrity_verified" in result_corrupted["readiness"]["blockers"]


def test_historical_approval_events_remain_unchanged(tmp_path):
    """Historical approval events and receipt lookups must work identically after sealing."""
    db_path = tmp_path / "historical_approvals.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                agent TEXT NOT NULL,
                event TEXT NOT NULL,
                details TEXT
            )
            """
        )
        plan_approval = json.dumps({"plan_hash": "plan-hash-999", "approved_by": "human_reviewer"})
        test_approval = json.dumps({"bundle_hash": "bundle-777", "test_review_hash": "bundle-777"})
        policy_approval = json.dumps({"exception_id": "sec-001", "decision": "approved"})

        conn.execute("INSERT INTO audit_events (id, workflow_id, timestamp, agent, event, details) VALUES (?, ?, ?, ?, ?, ?)",
                     (1, "wf-hist", "2026-10-18T00:00:00Z", "planner", "ENGINEERING_PLAN_AUTHORIZED", plan_approval))
        conn.execute("INSERT INTO audit_events (id, workflow_id, timestamp, agent, event, details) VALUES (?, ?, ?, ?, ?, ?)",
                     (2, "wf-hist", "2026-10-18T00:01:00Z", "reviewer", "TEST_RESULTS_APPROVED", test_approval))
        conn.execute("INSERT INTO audit_events (id, workflow_id, timestamp, agent, event, details) VALUES (?, ?, ?, ?, ?, ?)",
                     (3, "wf-hist", "2026-10-18T00:02:00Z", "policy", "POLICY_EXCEPTION_APPROVED", policy_approval))

    # Seal baseline
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        seal_legacy_baseline(conn)

    # Use standard audit approval queries to verify lookup behavior
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT details FROM audit_events WHERE workflow_id=? AND event=?",
                           ("wf-hist", "TEST_RESULTS_APPROVED")).fetchone()
        assert row is not None
        details = json.loads(row[0])
        assert details["bundle_hash"] == "bundle-777"

        policy_row = conn.execute(
            "SELECT event, details FROM audit_events WHERE workflow_id=? AND event IN ('POLICY_EXCEPTION_APPROVED')",
            ("wf-hist",)
        ).fetchone()
        assert policy_row is not None
        assert json.loads(policy_row[1])["exception_id"] == "sec-001"

    # Confirm chain is valid
    valid, report = verify_audit_integrity(db_path)
    assert valid is True
    assert report["total_records"] == 3
