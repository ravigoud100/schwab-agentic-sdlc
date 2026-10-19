"""Acceptance test verifying generic feature support across the full integrated SDLC lifecycle.

Tests that a feature unrelated to expiration (e.g. HTTP health-check endpoint)
executes through the standard entry point without feature-specific orchestration code,
with multiple dependent implementation tasks, human approvals, genuine Maven validation,
documentation, and release-readiness evaluation.
Also verifies that unsupported requirements fail closed safely.
"""
import json
from pathlib import Path
import shutil
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

import main
from governance import audit_logger
from governance.feature_registry import HealthCheckFeatureAdapter, get_feature_adapter
from governance.semantic_plan import health_check_contract
from llm.provider import ModelResponse

HEALTH_SERVICE_FILE = "url-shortener/src/main/java/url_shortener/UrlService.java"
HEALTH_CONTROLLER_FILE = "url-shortener/src/main/java/url_shortener/UrlController.java"
HEALTH_SERVICE_TEST = "url-shortener/src/test/java/url_shortener/UrlServiceTest.java"
HEALTH_CONTROLLER_TEST = "url-shortener/src/test/java/url_shortener/UrlApiContractIntegrationTest.java"
GENERATED_HEALTH_TEST = "url-shortener/src/test/java/url_shortener/GeneratedHealthCheckIntegrationTest.java"


class GenericDeterministicModel:
    """Deterministic model simulating the LLM across all SDLC stages for health-check."""

    def invoke(self, prompt, **kwargs):
        stage = kwargs.get("stage")

        if stage == "requirements":
            return ModelResponse(
                json.dumps({
                    "normalized_problem": "Add an HTTP health-check endpoint that returns system status",
                    "functional_requirements": [
                        "UrlService must provide getHealthStatus returning status UP",
                        "UrlController must provide /health GET endpoint returning the health status"
                    ],
                    "non_functional_requirements": [
                        "Fast response time",
                        "Preserve existing application behavior"
                    ],
                    "acceptance_criteria": [
                        "UrlService.getHealthStatus returns Map with status UP",
                        "GET /health returns HTTP 200 with status UP"
                    ],
                    "ambiguities": [
                        {
                            "id": "H1",
                            "question": "What health check format should be implemented?",
                            "options": ["HTTP Health Check Endpoint", "Simple boolean ping"],
                            "default_assumption": "HTTP Health Check Endpoint"
                        }
                    ],
                    "risks": [
                        "Existing routes remain unaffected"
                    ],
                    "classification": "brownfield"
                })
            )

        if stage == "design":
            contract = health_check_contract()
            return ModelResponse(
                json.dumps({
                    "api_contracts": [
                        {
                            "id": "health-endpoint",
                            "method": "GET",
                            "path": "/health",
                            "request_fields": [],
                            "validation_rules": [],
                            "response_behavior": ["Returns Map containing status UP"],
                        }
                    ],
                    "components": [
                        {"id": "service", "path": HEALTH_SERVICE_FILE, "responsibility": "Provides getHealthStatus method"},
                        {"id": "controller", "path": HEALTH_CONTROLLER_FILE, "responsibility": "Provides /health endpoint"},
                        {"id": "tests", "path": HEALTH_SERVICE_TEST, "responsibility": "Validates health check logic"},
                    ],
                    "interactions": [
                        {"from": "controller", "to": "service", "operation": "Calls getHealthStatus()"}
                    ],
                    "data_model_changes": [],
                    "compatibility_risks": [
                        {"id": "route-risk", "description": "Route conflict", "mitigation": "Distinct endpoint /health"}
                    ],
                    "architectural_decisions": [
                        {"id": "health-rest", "decision": "Expose /health via Spring REST controller", "rationale": "Standard HTTP monitoring"}
                    ],
                    "coverage": [
                        {
                            "obligation_id": "health-service-status",
                            "kind": "implementation",
                            "spec": contract["obligations"][0]["spec"],
                            "component_ids": ["service"],
                            "decision_ids": ["health-rest"]
                        },
                        {
                            "obligation_id": "health-endpoint-route",
                            "kind": "implementation",
                            "spec": contract["obligations"][1]["spec"],
                            "component_ids": ["controller"],
                            "decision_ids": ["health-rest"]
                        },
                        {
                            "obligation_id": "test-health-status",
                            "kind": "test",
                            "spec": contract["obligations"][2]["spec"],
                            "component_ids": ["tests"],
                            "decision_ids": ["health-rest"]
                        },
                        {
                            "obligation_id": "test-health-endpoint",
                            "kind": "test",
                            "spec": contract["obligations"][3]["spec"],
                            "component_ids": ["tests"],
                            "decision_ids": ["health-rest"]
                        }
                    ]
                })
            )

        if stage == "planning":
            tasks = [
                {
                    "execution_type": "implementation",
                    "task_id": "task-health-service",
                    "description": "Implement getHealthStatus method in UrlService returning Map with status UP",
                    "dependencies": [],
                    "allowed_files": [HEALTH_SERVICE_FILE, HEALTH_SERVICE_TEST],
                    "status": "PENDING"
                },
                {
                    "execution_type": "implementation",
                    "task_id": "task-health-controller",
                    "description": "Implement /health GET endpoint in UrlController calling UrlService.getHealthStatus",
                    "dependencies": ["task-health-service"],
                    "allowed_files": [HEALTH_CONTROLLER_FILE, HEALTH_CONTROLLER_TEST],
                    "status": "PENDING"
                }
            ]
            contract = health_check_contract()
            coverage = [
                {
                    "obligation_id": "health-service-status",
                    "kind": "implementation",
                    "spec": contract["obligations"][0]["spec"],
                    "task_id": "task-health-service",
                    "files": [HEALTH_SERVICE_FILE]
                },
                {
                    "obligation_id": "health-endpoint-route",
                    "kind": "implementation",
                    "spec": contract["obligations"][1]["spec"],
                    "task_id": "task-health-controller",
                    "files": [HEALTH_CONTROLLER_FILE]
                },
                {
                    "obligation_id": "test-health-status",
                    "kind": "test",
                    "spec": contract["obligations"][2]["spec"],
                    "task_id": "task-health-service",
                    "files": [HEALTH_SERVICE_TEST]
                },
                {
                    "obligation_id": "test-health-endpoint",
                    "kind": "test",
                    "spec": contract["obligations"][3]["spec"],
                    "task_id": "task-health-controller",
                    "files": [HEALTH_CONTROLLER_TEST]
                }
            ]
            plan = {
                "tasks": tasks,
                "coverage": coverage,
                "test_stage": {
                    "allowed_new_test_files": [GENERATED_HEALTH_TEST],
                    "protected_existing_test_files": [HEALTH_SERVICE_TEST, HEALTH_CONTROLLER_TEST]
                }
            }
            return ModelResponse(json.dumps(plan))

        if stage == "implementation":
            # Determine which task is being implemented based on Approved task block
            task_block = prompt.split("Approved task:\n")[1].split("\n")[0] if "Approved task:\n" in prompt else prompt
            if "UrlController.java" in task_block:
                old_text = "    public UrlController(UrlService urlService) {"
                new_text = (
                    "    @GetMapping(\"/health\")\n"
                    "    public Map<String, String> health() {\n"
                    "        return urlService.getHealthStatus();\n"
                    "    }\n\n"
                    "    public UrlController(UrlService urlService) {"
                )
                return ModelResponse(
                    json.dumps({
                        "edits": [
                            {"path": HEALTH_CONTROLLER_FILE, "old_text": old_text, "new_text": new_text}
                        ]
                    })
                )
            elif "UrlService.java" in task_block:
                old_text = "    @org.springframework.beans.factory.annotation.Autowired\n    public UrlService(UrlRepository repository) {"
                new_text = (
                    "    public Map<String, String> getHealthStatus() {\n"
                    "        return Map.of(\"status\", \"UP\");\n"
                    "    }\n\n"
                    "    @org.springframework.beans.factory.annotation.Autowired\n"
                    "    public UrlService(UrlRepository repository) {"
                )
                return ModelResponse(
                    json.dumps({
                        "edits": [
                            {"path": HEALTH_SERVICE_FILE, "old_text": old_text, "new_text": new_text}
                        ]
                    })
                )
            raise AssertionError(f"Unexpected implementation task in prompt: {task_block}")

        if stage == "test_generation":
            adapter = HealthCheckFeatureAdapter()
            return ModelResponse(json.dumps(adapter.expected_spec_payload()))

        if stage == "documentation":
            marker = "Authoritative evidence-derived fact catalog:\n"
            raw_catalog = prompt.split(marker)[1].split("\nPrevious validation errors:")[0]
            catalog = json.loads(raw_catalog)
            selection = {sec: list(items.keys()) for sec, items in catalog.items()}
            return ModelResponse(json.dumps(selection))

        raise AssertionError(f"Unexpected stage: {stage}")


def test_generic_feature_full_sdlc_lifecycle_with_genuine_maven(tmp_path, monkeypatch):
    """Executes an E2E health-check feature through standard main.workflow with multiple dependent tasks."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[1]
    src_app = Path("url-shortener") if Path("url-shortener").is_dir() else repo_root / "url-shortener"
    shutil.copytree(src_app, workspace / "url-shortener")
    (workspace / ".git").mkdir(exist_ok=True)

    db_path = tmp_path / "audit.db"
    monkeypatch.setattr(audit_logger, "DB_PATH", db_path)

    cache_dir = str(Path.home() / ".m2/repository")
    evidence_dir = tmp_path / "health-check-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    contract = health_check_contract()
    model = GenericDeterministicModel()
    monkeypatch.setattr(main, "model", model)

    initial_state = {
        "workflow_id": "health-check-e2e-acceptance-run",
        "workspace": str(workspace),
        "requirement": "Add an HTTP health-check endpoint that returns system status",
        "obligation_contract": contract,
        "integrated_sdlc": True,
        "integration_evidence": str(evidence_dir),
        "integration_cache": cache_dir,
        "plan_lineage_required": True,
        "design_required": True,
        "final_human_review_required": True,
        "implementation_response_format": "edits",
    }

    db_file = str(tmp_path / "graph.db")
    with SqliteSaver.from_conn_string(db_file) as saver:
        app = main.workflow.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "health-check-e2e-run"}}

        # 1. Start workflow -> Pauses at Clarification
        result = app.invoke(initial_state, config)
        assert len(result["__interrupt__"]) == 1
        assert result["__interrupt__"][0].value["type"] == "REQUIREMENT_CLARIFICATION"

        # 2. Resume -> Clarification approved -> Contract Review
        result = app.invoke(
            Command(resume={"decision": "approve", "answers": {"H1": "HTTP Health Check Endpoint"}}),
            config,
        )
        assert len(result["__interrupt__"]) == 1
        assert result["__interrupt__"][0].value["type"] == "REQUIREMENT_CONTRACT_APPROVAL"

        # 3. Resume -> Contract approved -> Design Generated -> Design Review
        result = app.invoke(Command(resume="approve"), config)
        assert len(result["__interrupt__"]) == 1
        assert result["__interrupt__"][0].value["type"] == "DESIGN_APPROVAL"

        # 4. Resume -> Design approved -> Planning Agent creates multiple dependent tasks -> Plan Approval
        result = app.invoke(Command(resume="approve"), config)
        assert len(result["__interrupt__"]) == 1
        assert "plan" in result["__interrupt__"][0].value

        plan_payload = json.loads(result["__interrupt__"][0].value["plan"])
        assert len(plan_payload["tasks"]) == 2
        assert plan_payload["tasks"][1]["dependencies"] == ["task-health-service"]
        assert plan_payload["test_stage"]["allowed_new_test_files"] == [GENERATED_HEALTH_TEST]

        # 5. Resume -> Plan approved -> Integrated SDLC executes:
        #    - Baseline Maven passes (32 tests)
        #    - Task 1 (UrlService) implemented & verified
        #    - Task 2 (UrlController) implemented & verified
        #    - Pauses at Test Generation Review
        result = app.invoke(Command(resume="approve"), config)
        assert len(result["__interrupt__"]) == 1
        assert result["__interrupt__"][0].value["type"] == "INTEGRATED_TEST_GENERATION_APPROVAL"

        # Verify source files were modified on disk by the two tasks
        service_code = (workspace / HEALTH_SERVICE_FILE).read_text()
        controller_code = (workspace / HEALTH_CONTROLLER_FILE).read_text()
        assert "getHealthStatus" in service_code
        assert "/health" in controller_code

        # 6. Resume -> Test Generation approved -> Test generated by vetted renderer -> Genuine Maven runs
        #    34 tests pass (32 baseline + 2 additive health-check tests)
        #    Pauses at Test Results Review
        result = app.invoke(Command(resume="approve"), config)
        assert len(result["__interrupt__"]) == 1
        assert result["__interrupt__"][0].value["type"] == "TEST_RESULTS_REVIEW"

        review_value = result["__interrupt__"][0].value
        assert review_value["workspace"] == str(workspace.resolve())
        records = review_value.get("records") or review_value.get("validation")
        assert records["baseline"]["exit_code"] == 0
        assert len(records["baseline"]["cases"]) == 32
        assert records["positive"]["exit_code"] == 0
        assert len(records["positive"]["cases"]) == 34
        assert "url_shortener.GeneratedHealthCheckIntegrationTest#healthStatusUp" in records["positive"]["cases"]
        assert "url_shortener.GeneratedHealthCheckIntegrationTest#controllerHealthEndpoint" in records["positive"]["cases"]

        # 7. Resume -> Test Results approved -> Documentation generated -> Release evaluated
        #    Pauses at Final Human Release Review
        result = app.invoke(Command(resume="approve"), config)
        assert len(result["__interrupt__"]) == 1
        assert result["__interrupt__"][0].value["type"] == "FINAL_RELEASE_REVIEW"
        report = result["__interrupt__"][0].value["report"]
        assert "checks" in report
        assert report["checks"]["documentation_verified"] is True
        assert report["checks"]["matching_implementation_plan_approved"] is True
        assert report["checks"]["human_test_results_review"] is True
        assert report["checks"]["release_security_and_operational_signoff"] is False
        doc_file = evidence_dir / "engineering-notes.md"
        assert doc_file.exists()
        assert len(doc_file.read_text()) > 0

        # 8. Resume -> Final human engineering approval
        result = app.invoke(Command(resume="approve"), config)
        assert result["status"] in {"FINAL_REVIEW_RECORDED", "STOPPED_RELEASE_BLOCKED"}
        assert not app.get_state(config).next


def test_unsupported_requirement_fails_closed():
    """Confirms unsupported requirement/contract fails closed safely with an explicit PermissionError."""
    unsupported_contract = {
        "decision_id": "UNSUPPORTED_DECISION_999",
        "selected_answer": "Arbitrary unvetted feature capability",
        "obligations": [
            {"id": "unknown-obligation", "kind": "implementation", "spec": {"action": "do_something"}}
        ]
    }
    with pytest.raises(PermissionError, match="Unsupported feature contract"):
        get_feature_adapter(unsupported_contract)
