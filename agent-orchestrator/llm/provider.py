import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from langchain_ollama import ChatOllama


@dataclass
class ModelResponse:
    text: str
    metadata: dict = field(default_factory=dict)


class ModelUnavailableError(Exception):
    """Raised when all authorized model attempts and fallbacks fail or are exhausted."""
    def __init__(self, message: str, stage: str | None = None, attempts: int = 0, last_error: Exception | None = None):
        super().__init__(message)
        self.stage = stage
        self.attempts = attempts
        self.last_error = last_error


class OllamaProvider:
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5-coder:latest"
        )
        self.model = ChatOllama(
            model=self.model_name,
            temperature=0,
            format=os.getenv("OLLAMA_FORMAT") or None,
            num_ctx=int(os.getenv('OLLAMA_NUM_CTX','32768')),
        )

    def invoke(self, prompt, *, stage: str | None = None):
        response = self.model.invoke(prompt)

        return ModelResponse(
            text=response.content,
            metadata=getattr(response,'response_metadata',{})
        )


SUPPORTED_STAGES = frozenset({
    "requirements", "codebase", "design", "planning", "implementation", "repair", "test_generation", "documentation",
})

def _read_evidence_file(rel_path: str) -> str:
    root = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "assessment-scenarios" / rel_path
    if root.exists():
        return root.read_text().strip()
    return ""


# These are synthetic review responses, not claims about the supplied source.
# Source-changing stages require vetted scenario fixtures before being enabled.
MOCK_SCENARIOS = {
    "url-expiration": {
        "requirements": json.dumps({
            "normalized_problem": "Verify existing URL expiration functionality",
            "functional_requirements": ["Verify optional URL expiration and existing expired URL behavior"],
            "non_functional_requirements": ["Preserve existing URL behavior"],
            "acceptance_criteria": ["Run the existing Maven test suite without changing Java source"],
            "ambiguities": [],
            "risks": ["Predefined review output does not establish correctness; real tests are required"],
            "classification": "brownfield",
        }),
        "codebase": (
            "Mock scenario: inspect url-shortener/src/main/java/url_shortener/"
            "ShortUrl.java for expiration support. This predefined response does "
            "not verify the actual source or endpoints."
        ),
        "planning": json.dumps({
            "tasks": [{
                "task_id": "verify-expiration",
                "execution_type": "validate_only",
                "description": (
                    "Inspect existing URL expiration functionality "
                    "and validate it using the Maven test suite."
                ),
                "dependencies": [],
                "allowed_files": [
                    "url-shortener/src/main/java/url_shortener/ShortUrl.java"
                ],
            }],
        }),
    },
    "greenfield-health": {
        "requirements": json.dumps({
            "normalized_problem": "Add a new URL health component that reports a stable ready status.",
            "functional_requirements": ["Add a new URL health component that reports a stable ready status."],
            "non_functional_requirements": ["A Spring component with no external I/O; no controller, dependency, configuration or existing test change."],
            "acceptance_criteria": ["Spring context starts", "existing tests remain executable"],
            "ambiguities": [],
            "risks": ["Component must be isolated"],
            "classification": "greenfield",
        }),
        "codebase": "Greenfield component analysis: url-shortener has standard Spring Boot structure.",
        "design": json.dumps({
            "component": "url-shortener/src/main/java/url_shortener/UrlHealthComponent.java",
            "contract": "UrlHealthComponent.status() returns the literal ready status.",
            "compatibility": "A Spring component with no external I/O; no controller, dependency, configuration or existing test change.",
        }),
        "planning": json.dumps({
            "tasks": [{
                "task_id": "add-url-health-component",
                "execution_type": "implementation",
                "description": "Create only UrlHealthComponent as an injectable Spring component with a deterministic ready status.",
                "dependencies": [],
                "allowed_files": [
                    "url-shortener/src/main/java/url_shortener/UrlHealthComponent.java"
                ],
            }],
        }),
        "implementation": _read_evidence_file("greenfield-execution/component-response.txt"),
        "test_generation": _read_evidence_file("greenfield-execution/workspace/url-shortener/src/test/java/url_shortener/UrlHealthComponentTest.java"),
        "documentation": "# UrlHealthComponent\n\nProvides a deterministic ready status check for the service.",
    },
    "brownfield-status": {
        "requirements": json.dumps({
            "normalized_problem": "Add a read-only URL status response without changing redirect click counting.",
            "functional_requirements": [
                "Add a read-only status method with no click increment.",
                "Expose GET /api/urls/{shortCode}/status"
            ],
            "non_functional_requirements": [
                "Unknown codes remain 404; redirect and analytics behavior remain unchanged."
            ],
            "acceptance_criteria": [
                "404 preserved",
                "status is read-only",
                "redirect clicks unchanged"
            ],
            "ambiguities": [],
            "risks": ["Redirect click count must remain untouched on status check"],
            "classification": "brownfield",
        }),
        "codebase": "Inspecting UrlService and UrlController for read-only status endpoint.",
        "design": json.dumps({
            "components": [
                "url-shortener/src/main/java/url_shortener/UrlService.java",
                "url-shortener/src/main/java/url_shortener/UrlController.java"
            ],
            "contract": "GET status reads a URL and reports short code and expiration state without incrementing clicks.",
            "compatibility": "Unknown codes remain 404; redirect and analytics behavior remain unchanged."
        }),
        "planning": json.dumps({
            "tasks": [
                {
                    "task_id": "status-service",
                    "execution_type": "implementation",
                    "dependencies": [],
                    "description": "Add a read-only status method with no click increment.",
                    "allowed_files": [
                        "url-shortener/src/main/java/url_shortener/UrlService.java"
                    ]
                },
                {
                    "task_id": "status-controller",
                    "execution_type": "implementation",
                    "dependencies": ["status-service"],
                    "description": "Expose the approved status response and preserve 404 behavior.",
                    "allowed_files": [
                        "url-shortener/src/main/java/url_shortener/UrlController.java"
                    ]
                }
            ]
        }),
        "implementation": _read_evidence_file("brownfield-autonomous-run/attempt-1-response.txt"),
        "test_generation": _read_evidence_file("brownfield-autonomous-run/workspace/url-shortener/src/test/java/url_shortener/UrlStatusIntegrationTest.java"),
        "documentation": "# URL Status API\n\nGET /api/urls/{shortCode}/status reports URL expiration and code without incrementing clicks.",
    },
    "ambiguous-expiration": {
        "requirements": _read_evidence_file("ambiguous-run/requirements-response.txt"),
        "codebase": "Inspecting ShortUrl.java for URL expiration mechanisms.",
        "design": json.dumps({
            "component": "url-shortener/src/main/java/url_shortener/ShortUrl.java",
            "contract": "Caller-provided future UTC timestamp for expiration.",
            "compatibility": "Existing links without expiration remain active."
        }),
        "planning": _read_evidence_file("ambiguous-run/planning-response.txt"),
    },
}


class MockProvider:
    def __init__(self, scenario: str | None = None):
        self.scenario = scenario if scenario is not None else os.getenv("MOCK_SCENARIO")

    def invoke(self, prompt, *, stage: str | None = None):
        if stage is None:
            raise NotImplementedError("Mock calls require an explicit workflow stage")
        if stage not in SUPPORTED_STAGES:
            raise ValueError(f"Unsupported workflow stage: {stage}")
        responses = MOCK_SCENARIOS.get(self.scenario, {})
        if stage not in responses:
            raise NotImplementedError(
                f"No vetted mock response for scenario {self.scenario!r}, stage {stage!r}"
            )
        metadata = {
            "execution_mode": "offline_fixture_replay",
            "provenance": "OFFLINE_FIXTURE_REPLAY",
            "scenario": self.scenario,
            "stage": stage,
        }
        return ModelResponse(text=responses[stage], metadata=metadata)


class ResilientModelProvider(OllamaProvider):
    """Wraps model execution with bounded retries, real fallback, and live mock prohibition."""
    def __init__(
        self,
        primary=None,
        fallback=None,
        max_retries: int = 2,
        is_live: bool = False,
        workflow_id: str | None = None,
        attempts_db: Path | str | None = None,
    ):
        self.primary = primary if primary is not None else OllamaProvider()
        self.fallback = fallback
        self.max_retries = max_retries
        self.is_live = is_live
        self.workflow_id = workflow_id
        self.attempts_db = Path(attempts_db) if attempts_db else None
        self.model = getattr(self.primary, 'model', None)
        self.model_name = getattr(self.primary, 'model_name', 'qwen2.5-coder:latest')

    def invoke(self, prompt: str, *, stage: str | None = None) -> ModelResponse:
        from governance.audit_logger import log_event
        wid = self.workflow_id or "resilient-model"

        # Prohibit mock fallback in live workflows
        if self.is_live and self.fallback is not None and isinstance(self.fallback, MockProvider):
            raise PermissionError("Mock provider responses are strictly prohibited in live workflows")

        # Check durable attempt ledger budget if present
        if self.attempts_db and self.attempts_db.is_file():
            import sqlite3
            try:
                with sqlite3.connect(self.attempts_db) as conn:
                    rows = conn.execute(
                        "SELECT ordinal, state FROM model_attempts WHERE workflow_id=? ORDER BY ordinal",
                        (wid,)
                    ).fetchall()
                    if rows and max(r[0] for r in rows) >= 3 and all(r[1] in {'FAILED', 'INTERRUPTED'} for r in rows):
                        raise PermissionError("Attempt budget exhausted in durable ledger")
            except sqlite3.OperationalError:
                pass

        attempts = 0
        last_error = None
        primary_name = getattr(self.primary, "model_name", type(self.primary).__name__)

        # 1. Primary provider with bounded retries
        for attempt_idx in range(self.max_retries + 1):
            attempts += 1
            try:
                return self.primary.invoke(prompt, stage=stage)
            except Exception as e:
                last_error = e
                if isinstance(e, (PermissionError, NotImplementedError)):
                    raise

        # Primary retries exhausted
        log_event(
            workflow_id=wid,
            agent="resilient_provider",
            event="MODEL_RETRIES_EXHAUSTED",
            details={
                "stage": stage,
                "provider": primary_name,
                "attempts": attempts,
                "error_type": type(last_error).__name__,
            }
        )

        # 2. Configurable real-model fallback
        if self.fallback is not None:
            if self.is_live and isinstance(self.fallback, MockProvider):
                raise PermissionError("Mock provider responses are strictly prohibited in live workflows")

            fallback_name = getattr(self.fallback, "model_name", type(self.fallback).__name__)
            log_event(
                workflow_id=wid,
                agent="resilient_provider",
                event="MODEL_PROVIDER_FALLBACK",
                details={
                    "stage": stage,
                    "primary_provider": primary_name,
                    "fallback_provider": fallback_name,
                    "error_type": type(last_error).__name__,
                }
            )

            fallback_attempts = 0
            for attempt_idx in range(self.max_retries + 1):
                fallback_attempts += 1
                attempts += 1
                try:
                    return self.fallback.invoke(prompt, stage=stage)
                except Exception as e:
                    last_error = e
                    if isinstance(e, (PermissionError, NotImplementedError)):
                        raise

            log_event(
                workflow_id=wid,
                agent="resilient_provider",
                event="MODEL_RETRIES_EXHAUSTED",
                details={
                    "stage": stage,
                    "provider": fallback_name,
                    "attempts": fallback_attempts,
                    "error_type": type(last_error).__name__,
                }
            )

        # 3. All authorized attempts exhausted
        log_event(
            workflow_id=wid,
            agent="resilient_provider",
            event="MODEL_UNAVAILABLE",
            details={
                "stage": stage,
                "total_attempts": attempts,
                "last_error": str(last_error),
                "error_type": type(last_error).__name__,
            }
        )
        raise ModelUnavailableError(
            f"All authorized model providers failed for stage {stage!r} after {attempts} attempts: {last_error}",
            stage=stage,
            attempts=attempts,
            last_error=last_error,
        )


def get_model():
    provider = os.getenv(
        "LLM_PROVIDER",
        "ollama"
    ).lower()

    if provider == "ollama":
        primary = OllamaProvider()
        fallback_model = os.getenv("OLLAMA_FALLBACK_MODEL")
        fallback = OllamaProvider(model_name=fallback_model) if fallback_model else None
        retries = int(os.getenv("MODEL_MAX_RETRIES", "2"))
        return ResilientModelProvider(primary=primary, fallback=fallback, max_retries=retries, is_live=True)

    if provider == "mock":
        return MockProvider()

    raise ValueError(
        f"Unsupported LLM provider: {provider}"
    )
