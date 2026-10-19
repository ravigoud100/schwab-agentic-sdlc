"""Tamper-evident SQLite audit logging with SHA-256 hash chaining.

Architectural Design & Security Boundaries:
1. Hash Chaining:
   Every record includes `prev_hash` and `row_hash`.
   - Genesis record (id=1): `prev_hash = "0" * 64`.
   - Subsequent records (id=N): `prev_hash = record[N-1].row_hash`.
   - `row_hash = SHA-256(prev_hash | id | workflow_id | timestamp | agent | event | canonical_details)`.

2. Concurrency & Transaction Safety:
   Writes serialize using `BEGIN IMMEDIATE` and `PRAGMA busy_timeout = 30000` with WAL journaling.
   This guarantees safe concurrent writes without hash-chain forks or race conditions.

3. Backward Compatibility & Legacy Baseline Sealing:
   Historical databases with unsealed records (NULL prev_hash/row_hash) are sealed chronologically
   from existing data into a baseline chain, strictly preserving historical payloads, IDs,
   timestamps, workflow IDs, and approval decisions.

4. Known Security Limitations (Explicit Threat Model):
   - Privileged Database Modification: An adversary with direct operating system write access to the
     SQLite file can modify a record and re-calculate all subsequent hashes to the tail. Local SQLite
     hash chaining detects unauthorized modifications, gaps, and race conditions, but cannot prevent
     privileged whole-chain reconstruction without an external root-of-trust (e.g. remote notary or KMS HMAC).
   - Undetectable Tail Truncation: Deleting the most recent K records leaves the remaining prefix chain
     valid from genesis. Tail deletion cannot be detected without an external checkpoint, signed receipt,
     or expected head hash.
   - Historical Authenticity: Baseline sealing of legacy records establishes a tamper-evident foundation
     from the point of sealing forward; it does not and cannot cryptographically prove the historical
     authenticity of events that occurred prior to sealing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[1] / "audit.db"
GENESIS_HASH = "0" * 64
BUSY_TIMEOUT_MS = 30000
CONNECT_TIMEOUT_SEC = 30.0


def canonical_json(val: Any) -> str:
    """Deterministic, sorted, whitespace-compact JSON serialization."""
    if val is None:
        return "{}"
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return json.dumps(val)
    return json.dumps(val, sort_keys=True, separators=(",", ":"))


def compute_row_hash(
    prev_hash: str,
    row_id: int,
    workflow_id: str,
    timestamp: str,
    agent: str,
    event: str,
    details_str: str,
) -> str:
    """Compute deterministic SHA-256 over canonical record payload."""
    payload = f"{prev_hash}|{row_id}|{workflow_id}|{timestamp}|{agent}|{event}|{details_str}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_connection(path: Path | str, timeout: float = CONNECT_TIMEOUT_SEC, read_only: bool = False) -> sqlite3.Connection:
    """Create a configured SQLite connection."""
    target = Path(path).resolve()
    if read_only:
        uri = f"file:{target}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return conn

    conn = sqlite3.connect(str(target), timeout=timeout, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure audit_events table exists with tamper-evident columns and indexes."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            agent TEXT NOT NULL,
            event TEXT NOT NULL,
            details TEXT,
            prev_hash TEXT,
            row_hash TEXT
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()}
    if "prev_hash" not in columns:
        conn.execute("ALTER TABLE audit_events ADD COLUMN prev_hash TEXT")
    if "row_hash" not in columns:
        conn.execute("ALTER TABLE audit_events ADD COLUMN row_hash TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_chain ON audit_events(id, row_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_workflow ON audit_events(workflow_id, id)")


def seal_legacy_baseline(conn: sqlite3.Connection) -> int:
    """Seal unchained historical audit records into a baseline hash chain.

    Labels historical backfilling as legacy baseline sealing. It does not
    claim to prove historical authenticity, but establishes a tamper-evident
    foundation for subsequent events while strictly preserving existing event
    payloads, IDs, workflow IDs, timestamps, and approvals.
    """
    rows = conn.execute(
        "SELECT id, workflow_id, timestamp, agent, event, details, prev_hash, row_hash FROM audit_events ORDER BY id ASC"
    ).fetchall()

    if not rows:
        return 0

    sealed_count = 0
    current_prev_hash = GENESIS_HASH

    for row in rows:
        row_id, workflow_id, timestamp, agent, event, details, prev_hash, row_hash = row

        if prev_hash is not None and row_hash is not None:
            current_prev_hash = row_hash
            continue

        details_str = details if details is not None else "{}"
        computed_hash = compute_row_hash(
            current_prev_hash, row_id, workflow_id, timestamp, agent, event, details_str
        )

        conn.execute(
            "UPDATE audit_events SET prev_hash = ?, row_hash = ? WHERE id = ?",
            (current_prev_hash, computed_hash, row_id),
        )
        current_prev_hash = computed_hash
        sealed_count += 1

    return sealed_count


def log_event(
    workflow_id: str,
    agent: str,
    event: str,
    details: dict | None = None,
) -> dict:
    """Log an event into the tamper-evident audit ledger with SHA-256 hash chaining.

    Uses BEGIN IMMEDIATE and SQLite busy timeout for thread and process safety.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    canonical_details = canonical_json(details or {})

    with _get_connection(DB_PATH) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            ensure_schema(conn)

            # If there are unsealed historical records, seal legacy baseline first
            cur = conn.execute("SELECT id FROM audit_events WHERE row_hash IS NULL LIMIT 1")
            if cur.fetchone() is not None:
                seal_legacy_baseline(conn)

            # Fetch the latest record in the chain
            cur = conn.execute("SELECT id, row_hash FROM audit_events ORDER BY id DESC LIMIT 1")
            last_row = cur.fetchone()
            if last_row:
                last_id, prev_hash = last_row[0], last_row[1]
                next_id = last_id + 1
            else:
                next_id = 1
                prev_hash = GENESIS_HASH

            row_hash = compute_row_hash(
                prev_hash, next_id, workflow_id, timestamp, agent, event, canonical_details
            )

            conn.execute(
                """
                INSERT INTO audit_events
                (id, workflow_id, timestamp, agent, event, details, prev_hash, row_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (next_id, workflow_id, timestamp, agent, event, canonical_details, prev_hash, row_hash),
            )
            conn.execute("COMMIT")
            return {
                "id": next_id,
                "workflow_id": workflow_id,
                "timestamp": timestamp,
                "agent": agent,
                "event": event,
                "details": canonical_details,
                "prev_hash": prev_hash,
                "row_hash": row_hash,
            }
        except Exception:
            conn.execute("ROLLBACK")
            raise


def verify_audit_integrity(
    db_path: Path | str | None = None,
    workflow_id: str | None = None,
    read_only: bool = False,
) -> tuple[bool, dict]:
    """Verify the integrity of the audit ledger.

    CRITICAL ALGORITHM RULE:
    Always validates the complete global audit chain from genesis to tail BEFORE
    evaluating workflow-specific presence. Any corruption in the database invalidates
    verification regardless of workflow_id.

    SECURITY LIMITATIONS:
    1. Privileged Database Rewrite: A user or process with OS write access to the database
       file can tamper with historical records and recompute all subsequent hashes to the tail.
       Local SQLite hash chaining detects accidental corruption, concurrent race conditions,
       and un-hashed modifications, but cannot prevent privileged whole-chain re-generation
       without external roots of trust.
    2. Undetectable Tail Deletion: Deleting the last K records leaves the preceding chain
       valid. Tail truncation cannot be detected without an external checkpoint, receipt,
       or expected head hash.
    """
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        return False, {"error": "DATABASE_NOT_FOUND", "message": f"Database file does not exist: {path}"}

    try:
        conn = _get_connection(path, read_only=read_only)
    except Exception as e:
        return False, {"error": "CONNECTION_FAILED", "message": str(e)}

    try:
        with conn:
            # Check if audit_events table exists
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
            ).fetchone()
            if not table_check:
                return False, {"error": "TABLE_NOT_FOUND", "message": "Table audit_events does not exist"}

            # Check for required columns
            columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()}
            if "prev_hash" not in columns or "row_hash" not in columns:
                return False, {
                    "error": "UNSEALED_LEGACY_SCHEMA",
                    "message": "Database contains legacy unsealed audit schema (prev_hash/row_hash missing)",
                }

            # Fetch all rows ordered by id
            rows = conn.execute(
                "SELECT id, workflow_id, timestamp, agent, event, details, prev_hash, row_hash "
                "FROM audit_events ORDER BY id ASC"
            ).fetchall()

            if not rows:
                if workflow_id:
                    return False, {
                        "error": "WORKFLOW_NOT_FOUND",
                        "message": f"No audit records found for workflow: {workflow_id}",
                    }
                return True, {"total_records": 0, "status": "EMPTY_CHAIN_VALID"}

            # Validate genesis record
            first_row = rows[0]
            first_id, first_wf, first_ts, first_agent, first_ev, first_dt, first_prev, first_hash = first_row

            if first_id != 1:
                return False, {
                    "error": "INVALID_GENESIS_ID",
                    "record_id": first_id,
                    "expected_id": 1,
                    "actual_id": first_id,
                    "message": f"Genesis record must have id=1, found id={first_id}",
                }

            if first_prev != GENESIS_HASH:
                return False, {
                    "error": "INVALID_GENESIS_HASH",
                    "record_id": first_id,
                    "expected_prev_hash": GENESIS_HASH,
                    "actual_prev_hash": first_prev,
                    "message": f"Genesis record must link to genesis sentinel '{GENESIS_HASH}'",
                }

            expected_first_hash = compute_row_hash(
                first_prev, first_id, first_wf, first_ts, first_agent, first_ev, first_dt or "{}"
            )
            if first_hash != expected_first_hash:
                return False, {
                    "error": "PAYLOAD_MODIFIED",
                    "record_id": first_id,
                    "workflow_id": first_wf,
                    "event": first_ev,
                    "expected_hash": expected_first_hash,
                    "actual_hash": first_hash,
                    "message": f"Payload hash mismatch at genesis record {first_id}",
                }

            prev_id = first_id
            prev_row_hash = first_hash
            workflow_records_count = 1 if (workflow_id and first_wf == workflow_id) else 0

            # Validate subsequent records
            for idx in range(1, len(rows)):
                row_id, r_wf, r_ts, r_agent, r_ev, r_dt, r_prev, r_hash = rows[idx]

                if r_hash is None or r_prev is None:
                    return False, {
                        "error": "UNSEALED_RECORD",
                        "record_id": row_id,
                        "message": f"Record {row_id} contains null hash values (unsealed)",
                    }

                # Monotonic sequence check (no deleted/spliced intermediate IDs)
                if row_id != prev_id + 1:
                    return False, {
                        "error": "MISSING_INTERMEDIATE_ID",
                        "record_id": row_id,
                        "expected_id": prev_id + 1,
                        "actual_id": row_id,
                        "message": f"ID sequence gap detected: expected {prev_id + 1}, found {row_id}",
                    }

                # Chain linkage check
                if r_prev != prev_row_hash:
                    return False, {
                        "error": "BROKEN_HASH_LINK",
                        "record_id": row_id,
                        "expected_prev_hash": prev_row_hash,
                        "actual_prev_hash": r_prev,
                        "message": f"Hash chain broken at record {row_id}: prev_hash does not match previous row_hash",
                    }

                # Payload fidelity check
                expected_hash = compute_row_hash(
                    r_prev, row_id, r_wf, r_ts, r_agent, r_ev, r_dt or "{}"
                )
                if r_hash != expected_hash:
                    return False, {
                        "error": "PAYLOAD_MODIFIED",
                        "record_id": row_id,
                        "workflow_id": r_wf,
                        "event": r_ev,
                        "expected_hash": expected_hash,
                        "actual_hash": r_hash,
                        "message": f"Payload hash mismatch at record {row_id}",
                    }

                prev_id = row_id
                prev_row_hash = r_hash
                if workflow_id and r_wf == workflow_id:
                    workflow_records_count += 1

            # Global chain is valid! Now check workflow presence if requested
            if workflow_id and workflow_records_count == 0:
                return False, {
                    "error": "WORKFLOW_NOT_FOUND",
                    "message": f"Global chain is valid ({len(rows)} records), but no records found for workflow: {workflow_id}",
                }

            return True, {
                "total_records": len(rows),
                "head_id": prev_id,
                "head_hash": prev_row_hash,
                "workflow_records": workflow_records_count if workflow_id else len(rows),
                "status": "VALID",
            }
    finally:
        conn.close()


def verify_audit_integrity_readonly(
    db_path: Path | str | None = None,
    workflow_id: str | None = None,
) -> tuple[bool, dict]:
    """Read-only verification wrapper that guarantees zero database modifications."""
    return verify_audit_integrity(db_path=db_path, workflow_id=workflow_id, read_only=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tamper-evident SQLite audit ledger verification.")
    parser.add_argument("--verify", action="store_true", help="Verify the cryptographic integrity of the audit chain.")
    parser.add_argument("--db", type=str, default=str(DB_PATH), help="Path to audit SQLite database.")
    parser.add_argument("--workflow-id", type=str, default=None, help="Optional workflow ID to verify within global chain.")
    parser.add_argument("--seal-legacy", action="store_true", help="Seal legacy historical records into a baseline hash chain.")

    args = parser.parse_args()

    if args.seal_legacy:
        db_file = Path(args.db)
        if not db_file.exists():
            print(f"Error: Database file not found: {db_file}", file=sys.stderr)
            sys.exit(2)
        with _get_connection(db_file) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                ensure_schema(conn)
                count = seal_legacy_baseline(conn)
                conn.execute("COMMIT")
                print(f"Successfully sealed {count} historical records into legacy baseline chain.")
                sys.exit(0)
            except Exception as e:
                conn.execute("ROLLBACK")
                print(f"Error sealing legacy baseline: {e}", file=sys.stderr)
                sys.exit(1)

    if args.verify:
        valid, report = verify_audit_integrity(args.db, workflow_id=args.workflow_id, read_only=True)
        if valid:
            print("AUDIT CHAIN INTEGRITY: VALID")
            print(f"  Database:        {args.db}")
            print(f"  Total records:   {report['total_records']}")
            print(f"  Chain Head ID:   {report.get('head_id')}")
            print(f"  Chain Head Hash: {report.get('head_hash')}")
            if args.workflow_id:
                print(f"  Workflow:        {args.workflow_id} ({report['workflow_records']} events)")
            sys.exit(0)
        else:
            print("AUDIT CHAIN INTEGRITY: FAILED", file=sys.stderr)
            print(f"  Database: {args.db}", file=sys.stderr)
            print(f"  Error:    {report.get('error')}", file=sys.stderr)
            print(f"  Message:  {report.get('message')}", file=sys.stderr)
            if "record_id" in report:
                print(f"  Failed Record ID: {report.get('record_id')}", file=sys.stderr)
            if "expected_hash" in report:
                print(f"  Expected: {report.get('expected_hash')}", file=sys.stderr)
                print(f"  Actual:   {report.get('actual_hash')}", file=sys.stderr)
            sys.exit(1)

    parser.print_help()
    sys.exit(0)


if __name__ == "__main__":
    main()