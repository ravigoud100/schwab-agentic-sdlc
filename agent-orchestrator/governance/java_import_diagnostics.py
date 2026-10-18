"""Conservative read-only Java import diagnostics for common SDLC types.

Analyzes Java source code for common types used in Spring Boot URL shortener
requirements (e.g., Instant, Map, List, Optional, ResponseStatusException).
Avoids treating comments or string literals as executable references.
Correctly handles fully qualified references (e.g. java.time.Instant.now()).
"""
import re

# Documented set of common types and their canonical package targets.
COMMON_TYPE_MAPPINGS = {
    "Instant": "java.time.Instant",
    "Duration": "java.time.Duration",
    "LocalDateTime": "java.time.LocalDateTime",
    "LocalDate": "java.time.LocalDate",
    "Clock": "java.time.Clock",
    "Map": "java.util.Map",
    "List": "java.util.List",
    "Set": "java.util.Set",
    "Optional": "java.util.Optional",
    "UUID": "java.util.UUID",
    "ResponseStatusException": "org.springframework.web.server.ResponseStatusException",
    "HttpStatus": "org.springframework.http.HttpStatus",
    "Transactional": "org.springframework.transaction.annotation.Transactional",
    "Autowired": "org.springframework.beans.factory.annotation.Autowired",
}


def strip_comments_and_strings(java_code: str) -> str:
    """Strip comments and string/char literals while preserving newlines and spacing."""
    result = []
    i = 0
    n = len(java_code)
    while i < n:
        # Text blocks """..."""
        if java_code[i:i + 3] == '"""':
            end = java_code.find('"""', i + 3)
            if end == -1:
                end = n
            else:
                end += 3
            newlines = java_code[i:end].count("\n")
            result.append("\n" * newlines if newlines else " ")
            i = end
        # Regular string literals "..."
        elif java_code[i] == '"':
            i += 1
            while i < n and java_code[i] != '"':
                if java_code[i] == "\\" and i + 1 < n:
                    i += 2
                else:
                    i += 1
            i += 1  # consume closing quote
            result.append(" ")
        # Char literals '...'
        elif java_code[i] == "'":
            i += 1
            while i < n and java_code[i] != "'":
                if java_code[i] == "\\" and i + 1 < n:
                    i += 2
                else:
                    i += 1
            i += 1  # consume closing quote
            result.append(" ")
        # Block comments /* ... */
        elif java_code[i:i + 2] == "/*":
            end = java_code.find("*/", i + 2)
            if end == -1:
                end = n
            else:
                end += 2
            newlines = java_code[i:end].count("\n")
            result.append("\n" * newlines if newlines else " ")
            i = end
        # Line comments // ...
        elif java_code[i:i + 2] == "//":
            end = java_code.find("\n", i + 2)
            if end == -1:
                end = n
            result.append(" ")
            i = end
        else:
            result.append(java_code[i])
            i += 1
    return "".join(result)


def extract_declared_imports(java_code: str) -> list[str]:
    """Extract list of imported package/class paths from Java code."""
    import_pattern = r"^\s*import\s+(?:static\s+)?([a-zA-Z0-9_.*]+)\s*;"
    imports = []
    for line in java_code.splitlines():
        m = re.match(import_pattern, line)
        if m:
            imports.append(m.group(1).strip())
    return imports


def extract_declared_package(java_code: str) -> str:
    """Extract package declaration if present."""
    m = re.search(r"^\s*package\s+([a-zA-Z0-9_.]+)\s*;", java_code, re.MULTILINE)
    return m.group(1).strip() if m else ""


def diagnose_java_imports(java_code: str, file_path: str = "") -> dict:
    """Analyze Java code for common unqualified types that lack an import statement.

    Returns a dict with 'valid' (bool), 'file_path' (str), 'declared_imports' (list),
    'unresolved_symbols' (list of dicts), and 'diagnostics' (list of str).
    """
    declared_imports = extract_declared_imports(java_code)
    stripped = strip_comments_and_strings(java_code)
    unresolved = []
    diagnostics = []

    # Map package wildcards (e.g. 'java.time.*' -> 'java.time')
    wildcard_packages = {
        imp[:-2] for imp in declared_imports if imp.endswith(".*")
    }

    for symbol, canonical in COMMON_TYPE_MAPPINGS.items():
        canonical_package = canonical.rsplit(".", 1)[0]
        # If explicitly imported or covered by wildcard, it is resolved
        if canonical in declared_imports or canonical_package in wildcard_packages:
            continue

        # Check if the file itself declares this type (e.g., `class Instant` or `record Instant`)
        if re.search(r"\b(?:class|interface|record|enum)\s+" + re.escape(symbol) + r"\b", stripped):
            continue

        # Search for unqualified occurrences:
        # Must not be preceded by a dot (e.g. `java.time.Instant`) or part of an identifier
        # Must not be part of package declaration or import statements (those are preceded by package/import)
        pattern = r"(?<![a-zA-Z0-9_$.])" + re.escape(symbol) + r"(?![a-zA-Z0-9_$])"
        matches = list(re.finditer(pattern, stripped))
        if not matches:
            continue

        # Check each match to ensure it's not in an import or package statement
        valid_unqualified_occurrences = []
        for m in matches:
            # Check line context
            line_start = stripped.rfind("\n", 0, m.start()) + 1
            line = stripped[line_start:m.start()].strip()
            if line.startswith("import ") or line.startswith("package "):
                continue
            line_no = stripped[:m.start()].count("\n") + 1
            valid_unqualified_occurrences.append(line_no)

        if valid_unqualified_occurrences:
            diag = (
                f"Unresolved symbol '{symbol}' in {file_path or 'generated source'}: "
                f"used at line(s) {valid_unqualified_occurrences[:3]} without import. "
                f"Add 'import {canonical};' or use fully qualified '{canonical}'."
            )
            unresolved.append({
                "symbol": symbol,
                "expected_import": canonical,
                "file_path": file_path,
                "lines": valid_unqualified_occurrences,
                "diagnostic": diag,
            })
            diagnostics.append(diag)

    return {
        "valid": len(unresolved) == 0,
        "file_path": file_path,
        "declared_imports": declared_imports,
        "unresolved_symbols": unresolved,
        "diagnostics": diagnostics,
    }


def diagnose_sources_imports(sources: dict[str, str]) -> dict:
    """Analyze a dictionary of {file_path: java_source_code}."""
    by_file = {}
    all_unresolved = []
    all_diagnostics = []

    for path, code in sources.items():
        if path.endswith(".java"):
            result = diagnose_java_imports(code, file_path=path)
            by_file[path] = result
            all_unresolved.extend(result["unresolved_symbols"])
            all_diagnostics.extend(result["diagnostics"])

    return {
        "valid": len(all_unresolved) == 0,
        "by_file": by_file,
        "unresolved_symbols": all_unresolved,
        "diagnostics": all_diagnostics,
        "summary": "\n".join(all_diagnostics) if all_diagnostics else "All common imports resolved.",
    }
