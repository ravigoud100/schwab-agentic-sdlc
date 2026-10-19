"""Regression tests for generated expiration boundary tests against the real Java UrlService.

Ensures that:
1. The constrained expiration test generator renders valid Java test cases using saveAndFlush().
2. The generated test file compiles and executes cleanly against the real Java UrlService using genuine Maven execution.
3. All four generated tests (futureExpiration, pastExpiration, equalNowExpiration, missingExpiration) pass.
4. If the generator reverts to incompatible repository methods (e.g. save()), Maven execution fails.
"""

from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from agents.test_generation_agent import render, CASES
from governance.semantic_plan import custom_expiration_contract

REPO_ROOT = Path(__file__).resolve().parents[1]


def _setup_isolated_maven_workspace(target_dir: Path) -> Path:
    """Copy url-shortener into target_dir without target artifacts or JDK cache."""
    src = REPO_ROOT / "url-shortener"
    dest = target_dir / "url-shortener"
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("target", ".jdk-25", "*.log")
    )
    return dest


def test_generated_expiration_tests_compile_and_pass_against_current_service(tmp_path):
    """Execute actual generator, write to isolated workspace, and run genuine Maven execution."""
    # 1. Generate the test specification and render Java test source
    payload = {"cases": [{"obligation_id": k, "spec": v} for k, v in CASES.items()]}
    contract = custom_expiration_contract()
    generated_bytes = render(payload, contract)
    generated_code = generated_bytes.decode()

    # Verify all four test obligations are present
    expected_tests = [
        "futureExpiration",
        "pastExpiration",
        "equalNowExpiration",
        "missingExpiration",
    ]
    for test_name in expected_tests:
        assert test_name in generated_code, f"Missing test obligation: {test_name}"
    assert "saveAndFlush" in generated_code

    # 2. Setup isolated workspace
    workspace = _setup_isolated_maven_workspace(tmp_path)
    test_dest = workspace / "src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java"
    test_dest.write_bytes(generated_bytes)

    # 3. Genuine Maven execution (no mocks)
    mvn_res = subprocess.run(
        ["./mvnw", "--batch-mode", "test", "-Dtest=GeneratedExpirationBoundaryTest"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=180
    )

    assert mvn_res.returncode == 0, f"Maven execution failed:\n{mvn_res.stdout}\n{mvn_res.stderr}"
    assert "BUILD SUCCESS" in mvn_res.stdout
    assert "Tests run: 4, Failures: 0, Errors: 0, Skipped: 0" in mvn_res.stdout

    # 4. Verify Surefire test report XML
    report_xml = workspace / "target/surefire-reports/TEST-url_shortener.GeneratedExpirationBoundaryTest.xml"
    assert report_xml.exists(), "Surefire test report XML was not generated"

    tree = ET.parse(report_xml)
    root = tree.getroot()
    assert int(root.attrib.get("tests", 0)) == 4
    assert int(root.attrib.get("failures", 0)) == 0
    assert int(root.attrib.get("errors", 0)) == 0
    assert int(root.attrib.get("skipped", 0)) == 0

    executed_case_names = {case.attrib.get("name") for case in root.findall("testcase")}
    assert set(expected_tests).issubset(executed_case_names)


def test_regression_fails_if_generator_uses_incompatible_save_method(tmp_path):
    """Ensure that using repository.save() fails genuine Maven execution against the current UrlService."""
    payload = {"cases": [{"obligation_id": k, "spec": v} for k, v in CASES.items()]}
    contract = custom_expiration_contract()
    generated_bytes = render(payload, contract)

    # Revert saveAndFlush to save to simulate the regression
    incompatible_bytes = generated_bytes.replace(b"saveAndFlush", b"save")
    assert b"saveAndFlush" not in incompatible_bytes

    workspace = _setup_isolated_maven_workspace(tmp_path)
    test_dest = workspace / "src/test/java/url_shortener/GeneratedExpirationBoundaryTest.java"
    test_dest.write_bytes(incompatible_bytes)

    mvn_res = subprocess.run(
        ["./mvnw", "--batch-mode", "test", "-Dtest=GeneratedExpirationBoundaryTest"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=180
    )

    assert mvn_res.returncode != 0, "Expected Maven execution to fail with incompatible save() call"
    assert "BUILD FAILURE" in mvn_res.stdout
    assert "Errors: 2" in mvn_res.stdout or "NullPointer" in mvn_res.stdout
