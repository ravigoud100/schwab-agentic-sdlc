import json
import os
from dataclasses import dataclass, field

from langchain_ollama import ChatOllama


@dataclass
class ModelResponse:
    text: str
    metadata: dict = field(default_factory=dict)


class OllamaProvider:
    def __init__(self):
        self.model = ChatOllama(
            model=os.getenv(
                "OLLAMA_MODEL",
                "qwen2.5-coder:latest"
            ),
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
        return ModelResponse(text=responses[stage])


def get_model():
    provider = os.getenv(
        "LLM_PROVIDER",
        "ollama"
    ).lower()

    if provider == "ollama":
        return OllamaProvider()

    if provider == "mock":
        return MockProvider()

    raise ValueError(
        f"Unsupported LLM provider: {provider}"
    )
