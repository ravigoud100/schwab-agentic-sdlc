from pathlib import Path

from langchain_ollama import ChatOllama


model = ChatOllama(
    model="qwen2.5-coder:latest",
    temperature=0,
    keep_alive=0,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
JAVA_FILE = (
    PROJECT_ROOT
    / ".agent-workspaces"
    / "url-expiration"
    / "url-shortener"
    / "src"
    / "main"
    / "java"
    / "url_shortener"
    / "ShortUrl.java"
)


def propose_repair(error_log: str) -> str:
    print("\n[REPAIR AGENT] Analyzing validation failure...")

    if not error_log.strip():
        raise ValueError("Validation error log cannot be empty")

    if not JAVA_FILE.is_file():
        raise FileNotFoundError(JAVA_FILE)

    actual_code = JAVA_FILE.read_text(encoding="utf-8")
    prompt = f"""You are a senior Java 21 Spring Boot engineer.

Analyze the Maven error against the actual Java source code.

Additional safety rules:
9. Never invent a root cause without supporting evidence.
10. If the reported missing method already exists in the source, explain the contradiction.
11. Do not propose changes when the evidence is insufficient.
12. Consider stale compilation output, mismatched source versions, or incorrect build configuration when errors contradict the code.
13. Never recommend disabling tests or bypassing validation.

MAVEN ERROR:
{error_log[-4000:]}

ACTUAL JAVA SOURCE:
{actual_code}

Propose a minimal repair. Do not modify files or execute commands."""

    response = model.invoke(prompt)
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)
