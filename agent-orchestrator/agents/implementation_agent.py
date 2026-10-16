from pathlib import Path
from langchain_ollama import ChatOllama

ALLOWED_FILE = (
    "url-shortener/src/main/java/"
    "url_shortener/ShortUrl.java"
)

model = ChatOllama(
    model="qwen2.5-coder:latest",
    temperature=0
)


def implement_expiration(approved: bool, workspace: Path):

    print("\n[IMPLEMENTATION AGENT] Starting...")

    # Governance: approval is mandatory
    if not approved:
        raise PermissionError(
            "Implementation blocked: human approval required"
        )

    if not workspace.is_dir():
        raise FileNotFoundError(
            "Isolated Git workspace does not exist"
        )

    target_file = (workspace / ALLOWED_FILE).resolve()
    workspace_root = workspace.resolve(strict=True)

    # Prevent writing outside the isolated workspace
    if not target_file.is_relative_to(workspace_root):
        raise PermissionError("Unauthorized file path")

    # Avoid overwriting an existing expiration implementation.
    existing_code = target_file.read_text(encoding="utf-8")
    required_elements = (
        "expiresAt",
        "getExpiresAt",
        "isExpired",
    )
    if all(element in existing_code for element in required_elements):
        print("[IMPLEMENTATION AGENT] Existing expiration code detected.")
        print("[IMPLEMENTATION AGENT] Skipping code generation.")
        return target_file

    original_code = target_file.read_text(encoding="utf-8")

    prompt = f"""
    You are a senior Java 21 Spring Boot developer.

    Modify the following JPA entity to support
    URL expiration.

    Requirements:
    1. Add an expiresAt field using java.time.Instant.
    2. Map expiresAt using @Column.
    3. Add getExpiresAt().
    4. Add isExpired(Instant now).
    5. Preserve existing fields, constructors, and methods.
    6. Do not remove existing functionality.
    7. Return the complete Java source code.
    8. Do not include Markdown formatting.

    Existing Java code:

    {original_code}
    """

    print("[IMPLEMENTATION AGENT] Generating Java code...")

    response = model.invoke(prompt)

    generated_code = response.content.strip()

    # Handle Markdown fences if the model includes them
    if generated_code.startswith("```"):
        lines = generated_code.splitlines()
        generated_code = "\n".join(lines[1:-1]).strip()

    # Basic structural checks before writing
    required_elements = [
        "package url_shortener;",
        "class ShortUrl",
        "expiresAt",
        "getExpiresAt",
        "isExpired"
    ]

    for element in required_elements:
        if element not in generated_code:
            raise ValueError(
                f"Generated code missing: {element}"
            )

    target_file.write_text(
        generated_code + "\n",
        encoding="utf-8"
    )

    print("[IMPLEMENTATION AGENT] File updated:")
    print(target_file)

    return target_file
