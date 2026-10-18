PYTHON := .venv/bin/python
VENV_PYTHON := python3.12
ORCHESTRATOR := agent-orchestrator
JAVA_PROJECT := url-shortener

.PHONY: setup test-python test-java test demo-mock

setup:
	$(VENV_PYTHON) -m venv .venv
	$(PYTHON) -m pip install -r $(ORCHESTRATOR)/requirements.txt

test-python:
	cd $(ORCHESTRATOR) && ../$(PYTHON) -m pytest \
		test_llm_provider.py \
		test_rollback.py \
		test_workspace_isolation.py \
		test_task_orchestration.py \
		test_replanning.py \
		test_repair_approval.py \
		test_repair_execution.py \
		test_workflow_scenarios.py -q

test-java:
	cd $(JAVA_PROJECT) && ./mvnw test

test: test-python test-java

demo-mock:
	cd $(ORCHESTRATOR) && \
	LLM_PROVIDER=mock MOCK_SCENARIO=url-expiration \
	../$(PYTHON) main.py
