# Ambiguous scenario: verification execution evidence

**Status: `AWAITING_AMBIGUOUS_RESULTS_REVIEW`**
**Verification approach: Option A — verification-only (no source modification)**

---

## Decision lineage

| Field | Value |
|---|---|
| Workflow ID | `06a48ae8-a82f-44e2-8232-74a7383f900c` |
| Ambiguity | A1 — "How is expiration selected?" |
| Human decision | **"Custom expiration timestamp"** (not "Fixed 24-hour TTL") |
| Decision recorded | `CLARIFICATION_APPROVED` in `ambiguous-run/audit.db`, event 3, 2026-10-17T01:59:44 UTC |
| Clarification req hash | `1de7e94476528dfd37493b75a48cc91b60b085cf152d34b710f7387139705b2d` |
| Plan hash (stored) | `e3726f8d5c537ce485dbd7c9f249e7668f8b4158749f1784974934e2b7818298` |

---

## Why verification-only

After reviewing the production checkout, the **"Caller-provided UTC timestamp" behavior is already present
in full**. The agentic analysis determined that no new implementation was required. This is a valid
agentic SDLC outcome: the agent reads the codebase, determines the requirement is satisfied, and
avoids unnecessary code generation.

The relevant implementation spans three existing files:

| File | Baseline SHA-256 | Key behavior |
|---|---|---|
| `UrlController.java` | `85480f3c…` | `CreateUrlRequest` accepts optional `java.time.Instant expiresAt` |
| `UrlService.java` | `98e42b3e…` | Rejects `expiresAt ≤ now` with HTTP 400; accepts null; stores future value |
| `ShortUrl.java` | `cdd60403…` | `isExpired(Instant now)`: `expiresAt != null && now.isAfter(expiresAt)` |

---

## Isolated workspace and pre/post-run integrity

- **Workspace:** `/private/tmp/schwab-assessment-ambiguous-verification/`
- **Method:** `cp -R` of production `url-shortener/` directory into isolated path
- All 8 source file hashes were recorded **before** the Maven run
- All 8 source file hashes were re-verified **after** the Maven run
- **Production checkout was not modified** — hashes identical before and after

| File | Pre-run SHA-256 | Post-run SHA-256 | Match |
|---|---|---|---|
| `UrlService.java` | `98e42b3e…` | `98e42b3e…` | ✅ |
| `UrlController.java` | `85480f3c…` | `85480f3c…` | ✅ |
| `ShortUrl.java` | `cdd60403…` | `cdd60403…` | ✅ |
| `RedirectController.java` | `c4a61615…` | `c4a61615…` | ✅ |
| `UrlRepository.java` | `579bf8f5…` | `579bf8f5…` | ✅ |
| `UrlShortenerApplication.java` | `be8101c1…` | `be8101c1…` | ✅ |
| `UrlExpirationIntegrationTests.java` | `43a670a8…` | `43a670a8…` | ✅ |
| `UrlShortenerApplicationTests.java` | `c7ed8576…` | `c7ed8576…` | ✅ |

---

## Maven validation

- **Java:** OpenJDK 21.0.12.1 (Eclipse Adoptium Temurin)
- **Command:** `./mvnw -o -Dmaven.repo.local=/private/tmp/schwab-test-generation/maven-cache clean test`
- **Exit code:** 0
- **Result:** `Tests run: 8, Failures: 0, Errors: 0, Skipped: 0`
- **Duration:** 3.447 s
- **Log:** [maven.log](maven.log)
- **Surefire XML:** [UrlExpirationIntegrationTests](surefire-reports/TEST-url_shortener.UrlExpirationIntegrationTests.xml), [UrlShortenerApplicationTests](surefire-reports/TEST-url_shortener.UrlShortenerApplicationTests.xml)

---

## Assertion analysis — required behaviors verified

The following table documents what each assertion **actually checks in code**, not just the test name.

| Required behavior | Test method | Assertion (verbatim) | Proven? |
|---|---|---|---|
| **Future UTC timestamp accepted** | `createsShortUrlWithFutureExpiration` | `assertEquals(Instant.parse("2099-01-01T00:00:00Z"), shortUrl.getExpiresAt())` — reads back from DB | ✅ Value stored exactly as provided |
| **Past UTC timestamp rejected (HTTP 400)** | `rejectsPastExpiration` | `POST expiresAt:"2020-01-01T00:00:00Z"` → `.andExpect(status().isBadRequest())` | ✅ HTTP 400 enforced |
| **Equal-to-now rejected** | (no dedicated test) | `UrlService`: `!expiresAt.isAfter(Instant.now())` — `isAfter` is strictly greater-than; equal fails and throws 400 | ✅ Logic correct; ⚠️ no dedicated boundary test with `Instant.now()` |
| **Missing expiration accepted** | `createsShortUrlWithoutExpiration` | POST with no `expiresAt` field → `status().isCreated()` + analytics returns URL | ✅ Null bypasses validation |
| **Expired URLs cannot redirect** | `returnsGoneForExpiredUrl` | Saves URL with `expiresAt=2020-01-01`, GET `/{code}` → `status().isGone()` | ✅ HTTP 410 returned |
| **No fixed 24-hour TTL applied** | `createsShortUrlWithFutureExpiration` | Stored `expiresAt` equals caller-supplied value `2099-01-01T00:00:00Z` exactly | ✅ No TTL override present |
| **Active URL redirects normally** | `redirectsActiveUrlWithFound` | GET `/{code}` → `status().isFound()` + `Location` header matches original URL; analytics shows `totalClicks=1` | ✅ |
| **Expired URL does not increment clicks** | `expiredUrlDoesNotIncreaseClickCount` | GET expired code → 410; analytics → `totalClicks=0` | ✅ |

### Documented gap
The exact-instant boundary (`expiresAt == Instant.now()`) has no dedicated test case.
The condition `!expiresAt.isAfter(now)` correctly rejects it because `isAfter` requires strict
inequality. The gap is a coverage observation, not a behavior defect. No test was added during
this verification; no source was modified.

---

## Scope compliance

- ✅ No production Java file was modified
- ✅ No test file was added or modified
- ✅ No build configuration, `pom.xml`, or property file was changed
- ✅ Fixed 24-hour TTL is not present anywhere in the codebase
- ✅ Ollama was not started or invoked
- ✅ No merge, release, or deployment occurred or is authorized

---

## Result summary

| Dimension | Finding |
|---|---|
| Requirement satisfied | ✅ "Custom expiration timestamp" behavior fully implemented |
| Test evidence | 7 expiration integration tests + 1 context test; all pass |
| Implementation provenance | Existing codebase — not AI-generated for this verification |
| Architecture | Caller-provided `Instant`, service-side validation, stored value, `isExpired()` at redirect time |
| Release authorization | ❌ None — awaiting human results review |
