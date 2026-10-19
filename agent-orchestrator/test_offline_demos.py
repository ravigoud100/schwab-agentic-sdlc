"""Automated tests for offline fixture replay demonstrations.

Validates that:
1. Greenfield demo replays approved UrlHealthComponent evidence sequentially.
2. Brownfield demo executes in an isolated pre-promotion Git baseline (ae28e81) and preserves the primary application.
3. Ambiguous requirement demo clarifies custom expiration without introducing a default TTL.
4. All demonstrations emit OFFLINE_FIXTURE_REPLAY audit events with provenance and disclaimers.
"""

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from governance.offline_demos import (
    run_greenfield_demo,
    run_brownfield_demo,
    run_ambiguous_demo,
    setup_pre_promotion_baseline,
    GREENFIELD_COMPONENT_HASH,
    GREENFIELD_TEST_HASH,
    BROWNFIELD_STATUS_TEST_HASH,
    AMBIGUOUS_CLARIFIED_REQ_HASH,
    AMBIGUOUS_PLAN_HASH,
    REPO_ROOT,
    EVIDENCE_ROOT,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_greenfield_demo_replays_approved_evidence(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_path)

    res = run_greenfield_demo(workflow_id="test-greenfield-demo")

    assert res["scenario"] == "greenfield"
    assert res["status"] == "GREENFIELD_RESULTS_APPROVED"
    assert res["component_hash"] == GREENFIELD_COMPONENT_HASH
    assert res["test_hash"] == GREENFIELD_TEST_HASH
    assert res["tests_passed"] == 9
    assert "GREENFIELD_MAVEN_VALIDATED" in res["stages"]
    assert res["execution_strategy"] == "sequential"
    assert res["provenance"] == "OFFLINE_FIXTURE_REPLAY"

    # Verify audit event
    with sqlite3.connect(db_path) as conn:
        events = conn.execute(
            "SELECT event, details FROM audit_events WHERE workflow_id=?",
            ("test-greenfield-demo",)
        ).fetchall()
    assert len(events) >= 1
    event_name, details_raw = events[0]
    assert event_name == "OFFLINE_FIXTURE_REPLAY"
    details = json.loads(details_raw)
    assert details["scenario"] == "greenfield"
    assert "NOT a live Ollama run" in details["disclaimer"]


def test_brownfield_demo_isolated_baseline_and_preserves_primary(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_path)

    # 1. Snapshot primary Java source hashes BEFORE demo
    primary_svc = REPO_ROOT / "url-shortener/src/main/java/url_shortener/UrlService.java"
    primary_ctrl = REPO_ROOT / "url-shortener/src/main/java/url_shortener/UrlController.java"
    primary_test = REPO_ROOT / "url-shortener/src/test/java/url_shortener/UrlStatusIntegrationTest.java"

    svc_before = _sha256(primary_svc.read_bytes())
    ctrl_before = _sha256(primary_ctrl.read_bytes())
    test_before = _sha256(primary_test.read_bytes())

    # 2. Run brownfield demo in isolated baseline
    res = run_brownfield_demo(workflow_id="test-brownfield-demo")

    assert res["scenario"] == "brownfield"
    assert res["status"] == "BROWNFIELD_RESULTS_APPROVED"
    assert res["maven_exit_code"] == 0
    assert res["tests_passed"] == 11
    assert "BROWNFIELD_MAVEN_VALIDATED" in res["stages"]
    assert res["isolated_baseline"] == "ae28e81"
    assert res["provenance"] == "OFFLINE_FIXTURE_REPLAY"
    assert res["test_hash"] == BROWNFIELD_STATUS_TEST_HASH

    # 3. Snapshot primary Java source hashes AFTER demo - MUST BE IDENTICAL
    svc_after = _sha256(primary_svc.read_bytes())
    ctrl_after = _sha256(primary_ctrl.read_bytes())
    test_after = _sha256(primary_test.read_bytes())

    assert svc_before == svc_after, "Primary UrlService was modified by demo!"
    assert ctrl_before == ctrl_after, "Primary UrlController was modified by demo!"
    assert test_before == test_after, "Primary UrlStatusIntegrationTest was modified by demo!"

    # 4. Confirm working tree remains clean
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    # The only untracked files should be the newly created test files or ignored files
    for line in status.stdout.splitlines():
        assert "url-shortener" not in line, f"url-shortener was unexpectedly touched: {line}"


def test_ambiguous_demo_clarification_without_default_ttl(tmp_path, monkeypatch):
    db_path = tmp_path / "audit.db"
    monkeypatch.setattr("governance.audit_logger.DB_PATH", db_path)

    res = run_ambiguous_demo(workflow_id="test-ambiguous-demo")

    assert res["scenario"] == "ambiguous"
    assert res["selected_option"] == "Custom expiration timestamp"
    assert "CLARIFICATION_APPROVED" in res["stages"]
    assert res["clarified_requirements_hash"] == AMBIGUOUS_CLARIFIED_REQ_HASH
    assert res["plan_hash"] == AMBIGUOUS_PLAN_HASH
    assert res["provenance"] == "OFFLINE_FIXTURE_REPLAY"

    # Verify audit event
    with sqlite3.connect(db_path) as conn:
        events = conn.execute(
            "SELECT event, details FROM audit_events WHERE workflow_id=?",
            ("test-ambiguous-demo",)
        ).fetchall()
    assert len(events) >= 1
    event_name, details_raw = events[0]
    assert event_name == "OFFLINE_FIXTURE_REPLAY"
    details = json.loads(details_raw)
    assert details["scenario"] == "ambiguous"
    assert "default TTL" in details["disclaimer"]


def test_pre_promotion_baseline_lacks_status_api(tmp_path):
    target = tmp_path / "baseline_check"
    setup_pre_promotion_baseline(target)

    svc_content = (target / "url-shortener/src/main/java/url_shortener/UrlService.java").read_text()
    assert "getStatus" not in svc_content
    assert not (target / "url-shortener/src/test/java/url_shortener/UrlStatusIntegrationTest.java").exists()
