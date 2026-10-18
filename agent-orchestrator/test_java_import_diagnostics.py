"""Regression tests for Java import diagnostics and type resolution rules.

Derives test cases directly from historical brownfield compilation failures
(missing java.time.Instant and missing java.util.Map), plus positive cases with
correct imports, fully qualified references, comments/strings, and wildcards.
"""
from governance.java_import_diagnostics import (
    diagnose_java_imports,
    diagnose_sources_imports,
    strip_comments_and_strings,
    extract_declared_imports,
)
from agents.source_generation import complete_source_prompt


# 1. Historical failed UrlController from Attempt 3 (missing import java.time.Instant)
HISTORICAL_FAILING_CONTROLLER = """package url_shortener;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.hibernate.validator.constraints.URL;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.net.URI;
import java.util.Map;

@RestController
@RequestMapping("/api/urls")
public class UrlController {

    private final UrlService urlService;

    public UrlController(UrlService urlService) {
        this.urlService = urlService;
    }

    public record CreateUrlRequest(
            @NotBlank
            @Size(max = 2048)
            @URL(protocol = "https")
            String originalUrl,
            java.time.Instant expiresAt
    ) {}

    @GetMapping("/{shortCode}/status")
    public Map<String, Object> getStatus(
            @PathVariable String shortCode) {

        ShortUrl shortUrl = urlService.getStatus(shortCode);

        return Map.of(
                "shortCode", shortUrl.getShortCode(),
                "expired", shortUrl.isExpired(Instant.now())
        );
    }
}
"""

# 2. Historical failing UrlService (using Map without import java.util.Map)
HISTORICAL_FAILING_SERVICE = """package url_shortener;

import org.springframework.stereotype.Service;
import java.security.SecureRandom;
import java.time.Instant;

@Service
public class UrlService {

    private final UrlRepository repository;

    public UrlService(UrlRepository repository) {
        this.repository = repository;
    }

    @org.springframework.transaction.annotation.Transactional(readOnly = true)
    public Map<String, Object> getStatus(String shortCode) {
        ShortUrl shortUrl = repository.findByShortCode(shortCode)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
        return Map.of("shortCode", shortUrl.getShortCode(), "expired", shortUrl.isExpired(Instant.now()));
    }
}
"""


def test_historical_missing_instant_failure():
    """Reproduces the exact historical Attempt 3 failure where UrlController used Instant.now() without import."""
    result = diagnose_java_imports(HISTORICAL_FAILING_CONTROLLER, "url_shortener/UrlController.java")
    assert result["valid"] is False
    assert len(result["unresolved_symbols"]) == 1
    unresolved = result["unresolved_symbols"][0]
    assert unresolved["symbol"] == "Instant"
    assert unresolved["expected_import"] == "java.time.Instant"
    assert "Unresolved symbol 'Instant'" in unresolved["diagnostic"]
    assert "java.time.Instant" in unresolved["diagnostic"]


def test_historical_missing_map_failure():
    """Reproduces the historical failure where UrlService returned Map without import java.util.Map."""
    result = diagnose_java_imports(HISTORICAL_FAILING_SERVICE, "url_shortener/UrlService.java")
    assert result["valid"] is False
    assert any(u["symbol"] == "Map" and u["expected_import"] == "java.util.Map" for u in result["unresolved_symbols"])


def test_correct_imports_pass():
    """UrlController with explicit import java.time.Instant; passes with zero unresolved symbols."""
    corrected_controller = HISTORICAL_FAILING_CONTROLLER.replace(
        "import java.util.Map;",
        "import java.time.Instant;\nimport java.util.Map;"
    )
    result = diagnose_java_imports(corrected_controller, "url_shortener/UrlController.java")
    assert result["valid"] is True
    assert len(result["unresolved_symbols"]) == 0
    assert result["diagnostics"] == []


def test_fully_qualified_references_pass():
    """Unimported types referenced via fully qualified package names pass without diagnostics."""
    qualified_controller = HISTORICAL_FAILING_CONTROLLER.replace(
        "shortUrl.isExpired(Instant.now())",
        "shortUrl.isExpired(java.time.Instant.now())"
    )
    result = diagnose_java_imports(qualified_controller, "url_shortener/UrlController.java")
    assert result["valid"] is True
    assert len(result["unresolved_symbols"]) == 0


def test_comments_and_strings_ignored():
    """Type names inside comments, string literals, and text blocks do not trigger false diagnostics."""
    source_with_comments_and_strings = """package url_shortener;

import org.springframework.stereotype.Service;

@Service
public class DummyService {
    // We should consider using Instant here
    /* Or maybe Map<String, Object> in a block comment */
    private String instantLabel = "Instant should not be flagged";
    private String mapLabel = "Map should not be flagged";
    private String textBlock = \"\"\"
        Instant now = Instant.now();
        Map<String, Object> map;
    \"\"\";
}
"""
    result = diagnose_java_imports(source_with_comments_and_strings, "url_shortener/DummyService.java")
    assert result["valid"] is True
    assert len(result["unresolved_symbols"]) == 0


def test_wildcard_imports_pass():
    """Package wildcard imports (e.g. import java.time.*;) resolve symbols in that package."""
    source_with_wildcards = """package url_shortener;

import java.time.*;
import java.util.*;

public class WildcardHelper {
    public Map<String, Object> getMap() {
        return Map.of("time", Instant.now());
    }
}
"""
    result = diagnose_java_imports(source_with_wildcards, "url_shortener/WildcardHelper.java")
    assert result["valid"] is True
    assert len(result["unresolved_symbols"]) == 0


def test_unsupported_or_unknown_types_unflagged():
    """Custom application types not in COMMON_TYPE_MAPPINGS are not flagged by the conservative checker."""
    custom_source = """package url_shortener;

public class CustomClass {
    private CustomRepository repository;
    public CustomEntity process(CustomInput input) {
        return null;
    }
}
"""
    result = diagnose_java_imports(custom_source, "url_shortener/CustomClass.java")
    assert result["valid"] is True
    assert len(result["unresolved_symbols"]) == 0


def test_diagnose_sources_imports_batch():
    """Batch diagnostic aggregates failures across multiple files and provides summary."""
    sources = {
        "UrlController.java": HISTORICAL_FAILING_CONTROLLER,
        "UrlService.java": HISTORICAL_FAILING_SERVICE,
    }
    batch_result = diagnose_sources_imports(sources)
    assert batch_result["valid"] is False
    assert len(batch_result["unresolved_symbols"]) >= 2
    assert "UrlController.java" in batch_result["summary"]
    assert "Instant" in batch_result["summary"]


def test_complete_source_prompt_includes_import_inventories_and_rules():
    """Prompt generator includes per-file import inventory, explicit rules, and notice for missing types."""
    sources = {
        "url-shortener/src/main/java/url_shortener/UrlController.java": HISTORICAL_FAILING_CONTROLLER,
        "url-shortener/src/main/java/url_shortener/UrlService.java": HISTORICAL_FAILING_SERVICE,
    }
    task = {"task_id": "status", "allowed_files": list(sources.keys())}
    context = {"import_diagnostics": "PREVIOUS_DIAG_SAMPLE"}
    prompt = complete_source_prompt(task, sources, context)

    assert "DECLARED IMPORTS PER FILE:" in prompt
    assert "TYPE RESOLUTION AND IMPORT OBLIGATIONS:" in prompt
    assert "UrlController.java does NOT currently import java.time.Instant" in prompt
    assert "UrlService.java does NOT currently import java.util.Map" in prompt
    assert "PREVIOUS_DIAG_SAMPLE" in prompt
