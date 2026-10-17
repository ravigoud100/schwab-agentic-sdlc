import json
from pathlib import Path

import pytest

from governance import audit_logger
from governance.assessment_scenarios import (
    GREENFIELD,
    ScenarioError,
    apply_approved_greenfield,
    catalog,
    finish_greenfield,
    prepare,
    validate_catalog,
)


def _workspace(tmp_path):
    root = tmp_path / "workspace"
    (root / "url-shortener/src/main/java/url_shortener").mkdir(parents=True)
    return root


def test_catalog_has_three_distinct_reviewable_scenarios():
    data = validate_catalog()
    assert data["greenfield"]["classification"] == "greenfield"
    assert data["greenfield"]["plan"]["tasks"][0]["allowed_new_files"] == [GREENFIELD]
    assert data["brownfield"]["plan"]["tasks"][1]["dependencies"] == ["status-service"]
    assert data["ambiguous"]["answers"]["Caller-provided UTC timestamp"]["plan_effect"] != data["ambiguous"]["answers"]["Fixed 24-hour TTL"]["plan_effect"]


def test_catalog_rejects_scope_or_ambiguity_regressions():
    data = catalog()
    data["greenfield"]["plan"]["tasks"][0]["allowed_files"] = ["url-shortener/pom.xml"]
    with pytest.raises(ScenarioError):
        validate_catalog(data)


def test_prepare_writes_review_packets_without_source_or_approval(tmp_path, monkeypatch):
    audit = tmp_path / "audit.db"
    monkeypatch.setattr(audit_logger, "DB_PATH", audit)
    result = prepare(tmp_path / "evidence", "scenario-review")
    assert result["status"] == "AWAITING_SCENARIO_PLAN_APPROVALS"
    assert result["source_writes"] == 0
    for name in ("greenfield", "brownfield", "ambiguous"):
        assert json.loads((tmp_path / "evidence" / f"{name}-proposal.json").read_text())["classification"]
    import sqlite3
    with sqlite3.connect(audit) as connection:
        assert connection.execute("select count(*) from audit_events where event='SCENARIO_REVIEW_REQUESTED'").fetchone()[0] == 3


def test_greenfield_new_file_requires_approval_and_rolls_back_safely(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_logger, "DB_PATH", tmp_path / "audit.db")
    root = _workspace(tmp_path)
    source = b"package url_shortener;\nimport org.springframework.stereotype.Component;\n@Component public class UrlHealthComponent {}\n"
    with pytest.raises(PermissionError):
        apply_approved_greenfield(root, "greenfield-test", source, approved=False)
    tx = apply_approved_greenfield(root, "greenfield-test", source, approved=True)
    target = root / GREENFIELD
    assert target.read_bytes() == source
    finish_greenfield(tx, root, passed=False)
    assert not target.exists()


def test_greenfield_new_file_is_validated_then_retained(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_logger, "DB_PATH", tmp_path / "audit.db")
    root = _workspace(tmp_path)
    source = b"package url_shortener;\nimport org.springframework.stereotype.Component;\n@Component public class UrlHealthComponent {}\n"
    tx = apply_approved_greenfield(root, "greenfield-test", source, approved=True)
    finish_greenfield(tx, root, passed=True)
    assert (root / GREENFIELD).read_bytes() == source
