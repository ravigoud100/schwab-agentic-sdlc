from unittest.mock import patch

from governance.task_orchestrator import (
    TaskPlanError,
    finish_tasks,
    independent_ready_batches,
    schedule_tasks,
    start_tasks,
    validate_task_plan,
)
from main import task_scheduler_node


def task(task_id, dependencies=None, files=None, status="PENDING"):
    return {
        "task_id": task_id,
        "description": task_id,
        "dependencies": dependencies or [],
        "status": status,
        "allowed_files": files or [f"src/{task_id}.java"],
    }


def test_dependency_ordering():
    tasks = [task("service", ["entity"]), task("entity")]
    scheduled, ready, _ = schedule_tasks(tasks)
    assert ready == ["entity"]
    running = start_tasks(scheduled, ready)
    completed = finish_tasks(running, ready, True)
    _, next_ready, _ = schedule_tasks(completed)
    assert next_ready == ["service"]


def test_circular_dependencies_are_rejected():
    with _raises(TaskPlanError):
        validate_task_plan([task("a", ["b"]), task("b", ["a"])])


def test_missing_dependencies_are_rejected():
    with _raises(TaskPlanError):
        validate_task_plan([task("service", ["missing"])])


def test_duplicate_task_ids_are_rejected():
    with _raises(TaskPlanError):
        validate_task_plan([task("same"), task("same")])


def test_independent_tasks_are_grouped_only_when_files_do_not_overlap():
    tasks = [task("entity", files=["src/entity.java"]), task("tests", files=["src/tests.java"])]
    assert independent_ready_batches(tasks) == [["entity", "tests"]]

    overlapping = [
        task("a", files=["src/shared.java"]),
        task("b", files=["src/shared.java"]),
    ]
    assert independent_ready_batches(overlapping) == [["a"], ["b"]]


def test_failed_task_blocks_dependents():
    tasks = [task("service", ["entity"]), task("entity")]
    scheduled, ready, _ = schedule_tasks(tasks)
    failed = finish_tasks(start_tasks(scheduled, ready), ready, False)
    assert {item["task_id"]: item["status"] for item in failed} == {
        "entity": "FAILED",
        "service": "BLOCKED",
    }


def test_approval_is_required_before_task_scheduling():
    state = {
        "workflow_id": "approval-task-test",
        "approval": "reject",
        "tasks": [task("implementation")],
    }
    with patch("main.log_event"):
        result = task_scheduler_node(state)
    assert result["task_plan_valid"] is False
    assert result["status"] == "STOPPED"


class _raises:
    def __init__(self, expected):
        self.expected = expected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None or not issubclass(exc_type, self.expected):
            raise AssertionError(f"Expected {self.expected.__name__}")
        return True


def test_parser_preserves_execution_type_and_legacy_defaults():
    import json
    from governance.task_orchestrator import tasks_from_plan

    original = task("verify-expiration")
    original["execution_type"] = "validate_only"
    parsed = tasks_from_plan(json.dumps({"tasks": [original]}), "src/entity.java")
    assert parsed[0]["execution_type"] == "validate_only"
    del original["execution_type"]
    parsed = tasks_from_plan(json.dumps({"tasks": [original]}), "src/entity.java")
    assert parsed[0]["execution_type"] == "implementation"
    assert tasks_from_plan("legacy prose", "src/entity.java")[0]["execution_type"] == "implementation"


def test_unknown_execution_type_is_rejected_without_fallback():
    import json
    import pytest
    from governance.task_orchestrator import tasks_from_plan

    for value in ("shell", None, [], 1):
        invalid = {**task("unknown"), "execution_type": value}
        with pytest.raises(TaskPlanError):
            validate_task_plan([invalid])
        with pytest.raises(TaskPlanError):
            tasks_from_plan(json.dumps({"tasks": [invalid]}), "src/entity.java")


def test_validation_routing_requires_unchanged_human_approved_plan():
    import json
    import pytest
    from main import human_approval, task_scheduler_router
    from governance.task_orchestrator import tasks_from_plan

    definition = {**task("verify-expiration"), "execution_type": "validate_only"}
    plan = json.dumps({"tasks": [definition]})
    state = {"requirement": "Verify expiration", "plan": plan, "approval": "approve",
             "tasks": tasks_from_plan(plan, "src/entity.java"),
             "active_task_ids": ["verify-expiration"], "task_plan_valid": True}
    with pytest.raises(PermissionError):
        task_scheduler_router(state)
    with patch("main.interrupt", return_value="approve"):
        state.update(human_approval(state))
    assert task_scheduler_router(state) == "verification"
    state["tasks"][0]["description"] = "unreviewed replacement"
    with pytest.raises(PermissionError):
        task_scheduler_router(state)
