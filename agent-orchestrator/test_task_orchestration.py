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
