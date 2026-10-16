import json
import os
from dataclasses import dataclass

from langchain_ollama import ChatOllama


@dataclass
class ModelResponse:
    text: str


class OllamaProvider:
    def __init__(self):
        self.model = ChatOllama(
            model=os.getenv(
                "OLLAMA_MODEL",
                "qwen2.5-coder:latest"
            ),
            temperature=0,
        )

    def invoke(self, prompt, *, stage: str | None = None):
        response = self.model.invoke(prompt)

        return ModelResponse(
            text=response.content
        )


SUPPORTED_STAGES = frozenset({
    "requirements", "codebase", "planning", "implementation", "repair",
})

# These are synthetic review responses, not claims about the supplied source.
# Source-changing stages require vetted scenario fixtures before being enabled.
MOCK_SCENARIOS = {
    "url-expiration": {
        "requirements": (
            "Mock scenario: URL expiration. Verify the existing expiration timestamp; "
            "preserve existing URL behavior. Review boundary semantics and test "
            "expired, unexpired, and unset timestamps before approval."
        ),
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
