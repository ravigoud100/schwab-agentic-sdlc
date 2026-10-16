from pathlib import Path
from langchain_ollama import ChatOllama

def analyze_codebase(requirement: str, workspace: Path) -> str:

    print("\n[CODEBASE AGENT] Inspecting Java application...")

    workspace_root = workspace.resolve(strict=True)
    java_source = (workspace_root / "url-shortener/src/main/java").resolve(strict=True)
    if not java_source.is_relative_to(workspace_root):
        raise PermissionError("Source path escapes isolated workspace")

    if not java_source.is_dir():
        raise FileNotFoundError(
            f"Java source directory not found: {java_source}"
        )

    source_files = sorted(java_source.rglob("*.java"))

    if not source_files:
        raise RuntimeError("No Java source files found")

    code_context = ""

    for file in source_files:

        # Only read files inside our intended Java directory.
        relative_path = file.relative_to(java_source)

        if not file.resolve().is_relative_to(workspace_root):
            raise PermissionError("Source file escapes isolated workspace")

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