from pathlib import Path
from langchain_ollama import ChatOllama

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JAVA_SOURCE = PROJECT_ROOT / "url-shortener/src/main/java"


def analyze_codebase(requirement: str) -> str:

    print("\n[CODEBASE AGENT] Inspecting Java application...")

    if not JAVA_SOURCE.is_dir():
        raise FileNotFoundError(
            f"Java source directory not found: {JAVA_SOURCE}"
        )

    source_files = sorted(JAVA_SOURCE.rglob("*.java"))

    if not source_files:
        raise RuntimeError("No Java source files found")

    code_context = ""

    for file in source_files:

        # Only read files inside our intended Java directory.
        relative_path = file.relative_to(JAVA_SOURCE)

        content = file.read_text(encoding="utf-8")

        code_context += f"""

        FILE: {relative_path}

        {content}

        """

    model = ChatOllama(
        model="qwen2.5-coder:latest",
        temperature=0
    )

    prompt = f"""
    You are a senior Java software architect.

    Analyze the following EXISTING Spring Boot codebase.

    User Requirement:
    {requirement}

    Actual Java source code:
    {code_context}

    Produce:

    1. Existing application architecture.
    2. Actual REST endpoints found in the code.
    3. Existing classes and their responsibilities.
    4. Files affected by the new requirement.
    5. New classes or dependencies potentially needed.
    6. Risks and compatibility concerns.
    7. Assumptions requiring human approval.

    Rules:
    - Base claims about existing code only on the supplied files.
    - Do not invent existing classes or endpoints.
    - Clearly distinguish facts from proposed changes.
    - Do not modify any code.
    """

    response = model.invoke(prompt)

    return response.text