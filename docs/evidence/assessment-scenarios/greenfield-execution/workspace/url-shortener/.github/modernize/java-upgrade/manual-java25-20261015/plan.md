# Upgrade Plan: url-shortener (manual-java25-20261015)

- **Generated**: 2026-10-15
- **HEAD Branch**: main
- **HEAD Commit ID**: N/A (telemetry/version-control helper did not expose the commit ID)

## Available Tools

**JDKs**
- JDK 21: not available (baseline will be skipped)
- JDK 25: **<TO_BE_INSTALLED>** (required for final verification; installation helper unavailable)

**Build Tools**
- Maven Wrapper 3.10.0: `./mvnw` (available; no upgrade required)

## Guidelines

- Upgrade the Java runtime to the latest LTS version in auto-execution mode.

> Note: You can add any specific guidelines or constraints for the upgrade process here if needed, bullet points are preferred.

## Options

- Working branch: appmod/java-upgrade-manual-java25-20261015
- Run tests before and after the upgrade: true

## Upgrade Goals

- Java runtime: 25

## Technology Stack

| Technology/Dependency | Current | Min Compatible Version | Why Incompatible |
|---|---:|---:|---|
| Java | 21 | 25 | User requested latest LTS |
| Spring Boot | 4.0.8 | 4.0.8 | Already compatible with Java 25 |
| Maven Wrapper | 3.10.0 | 3.9+ | Compatible; retained |
| Maven Compiler Plugin | Spring Boot managed | Java 25-compatible managed version | Managed by Spring Boot parent |

## Derived Upgrades

- No framework upgrade is required. Spring Boot 4.0.8 already supports the requested Java runtime.
- The Maven wrapper is retained because Maven 3.10.0 is compatible with Java 25.
- No Kotlin sources or Kotlin build configuration were detected, so no Kotlin upgrade is required.

## Impact Analysis

### Dependency Changes

| File | Dependency | Current | Action | Target | Reason |
|---|---|---:|---|---:|---|
| `pom.xml` | `java.version` | 21 | upgrade | 25 | Set compiler/runtime target to Java 25 |

### Source Code Changes

No source changes are required; the code uses standard Java and Jakarta APIs already compatible with the existing Spring Boot 4 baseline.

### Configuration Changes

No application configuration changes are required.

### CI/CD Changes

No CI/CD or container files reference a hardcoded Java version.

### Risks & Warnings

- **Local JDK unavailable**: The workspace has no discoverable Java runtime, and the JDK installation helper failed. Mitigation: install a JDK 25 locally, then rerun `./mvnw clean test`.
- **Baseline unavailable**: Java 21 was not available, so a pre-upgrade baseline cannot be measured. The existing test suite remains the acceptance target.

## Upgrade Steps

- Step 1: Setup Environment
  - **Rationale**: Ensure Java 25 and the Maven wrapper are available.
  - **Changes to Make**: Install or select JDK 25; retain Maven Wrapper 3.10.0.
  - **Verification**: `java -version` reports 25; `./mvnw -version` reports Maven 3.10.0.

- Step 2: Setup Baseline
  - **Rationale**: Establish pre-upgrade build/test results when the current JDK is available.
  - **Changes to Make**: None.
  - **Verification**: Skipped because JDK 21 is unavailable.

- Step 3: Set Java 25 Target
  - **Rationale**: Apply the requested runtime target.
  - **Changes to Make**: Update `pom.xml` `java.version` from 21 to 25.
  - **Verification**: `./mvnw clean test-compile`; expected successful main and test compilation under JDK 25.

- Step 4: Final Validation
  - **Rationale**: Confirm the target runtime and full regression suite.
  - **Changes to Make**: Resolve any Java 25 compilation or test failures.
  - **Verification**: `./mvnw clean test`; expected 100% pass rate.

- CVE Validation & Fix: No dependency versions were changed; validate direct dependencies if the security helper becomes available.
