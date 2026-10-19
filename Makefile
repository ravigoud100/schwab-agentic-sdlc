PYTHON := .venv/bin/python
VENV_PYTHON := python3.12
ORCHESTRATOR := agent-orchestrator
JAVA_PROJECT := url-shortener

.PHONY: setup test-python test-java test demo-mock demo-greenfield demo-brownfield demo-ambiguous demo-offline cache-maven verify-audit validate-env help

help:
	@echo "Available targets:"
	@echo "  setup            - Create virtual environment and install Python dependencies"
	@echo "  cache-maven      - Download and cache all Maven dependencies offline"
	@echo "  validate-env     - Run startup validation checks for Python, Java, and Maven"
	@echo "  test-python      - Run full offline Python regression suite (540+ tests)"
	@echo "  test-java        - Run Java Spring Boot integration test suite (32 tests)"
	@echo "  test             - Run both Python and Java test suites"
	@echo "  demo-greenfield  - Run offline Greenfield URL health component demonstration"
	@echo "  demo-brownfield  - Run offline Brownfield status API demonstration"
	@echo "  demo-ambiguous   - Run offline Ambiguous requirement clarification demonstration"
	@echo "  demo-offline     - Run all three offline fixture demonstrations"
	@echo "  verify-audit     - Verify cryptographic hash chain integrity of audit log"

setup:
	$(VENV_PYTHON) -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r $(ORCHESTRATOR)/requirements.txt
	chmod +x $(JAVA_PROJECT)/mvnw

cache-maven:
	cd $(JAVA_PROJECT) && ./mvnw dependency:go-offline

validate-env:
	cd $(ORCHESTRATOR) && ../$(PYTHON) -c "from governance.startup_validation import validate_environment; print('Environment check:', validate_environment())"

test-python:
	cd $(ORCHESTRATOR) && \
	LLM_PROVIDER=mock MOCK_SCENARIO=url-expiration \
	../$(PYTHON) -m pytest . -q

test-java:
	cd $(JAVA_PROJECT) && ./mvnw clean test

test: test-python test-java

demo-greenfield:
	$(PYTHON) $(ORCHESTRATOR)/main.py --demo greenfield

demo-brownfield:
	$(PYTHON) $(ORCHESTRATOR)/main.py --demo brownfield

demo-ambiguous:
	$(PYTHON) $(ORCHESTRATOR)/main.py --demo ambiguous

demo-offline: demo-ambiguous demo-greenfield demo-brownfield

demo-mock:
	cd $(ORCHESTRATOR) && \
	LLM_PROVIDER=mock MOCK_SCENARIO=url-expiration \
	../$(PYTHON) main.py

verify-audit:
	$(PYTHON) $(ORCHESTRATOR)/main.py --verify-audit
