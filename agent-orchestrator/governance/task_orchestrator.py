from copy import deepcopy


EXECUTION_TYPES = {"implementation", "validate_only"}


TASK_STATES = {
    "PENDING",
    "READY",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "BLOCKED",
}


class TaskPlanError(ValueError):
    """Raised when a task plan is not a valid executable DAG."""


REQUIRED_TASK_FIELDS = {
    "task_id", "description", "dependencies", "status", "allowed_files"
}


def _validate_task_shape(task: dict, task_ids: set[str]) -> None:
    if not isinstance(task, dict) or not REQUIRED_TASK_FIELDS.issubset(task) or set(task) - (REQUIRED_TASK_FIELDS | {"execution_type"}):
        raise TaskPlanError("Each task must have exactly the required fields")
    task_id = task["task_id"]
    if not isinstance(task_id, str) or not task_id.strip():
        raise TaskPlanError("Task IDs must be non-empty strings")
    if task_id in task_ids:
        raise TaskPlanError(f"Duplicate task ID: {task_id}")
    if task["status"] not in TASK_STATES:
        raise TaskPlanError(f"Invalid status for task {task_id}")
    if not isinstance(task["dependencies"], list) or not all(
        isinstance(dep, str) for dep in task["dependencies"]
    ):
        raise TaskPlanError(f"Invalid dependencies for task {task_id}")
    if not isinstance(task["allowed_files"], list) or not all(
        isinstance(path, str) and path and not path.startswith("/")
        and ".." not in path.split("/")
        for path in task["allowed_files"]
    ):
        raise TaskPlanError(f"Invalid allowed_files for task {task_id}")
    task_ids.add(task_id)


def _assert_dependencies_exist(tasks: list[dict], task_ids: set[str]) -> None:
    for task in tasks:
        missing = set(task["dependencies"]) - task_ids
        if missing:
            raise TaskPlanError(
                f"Task {task['task_id']} has missing dependencies: {sorted(missing)}"
            )


def _assert_acyclic(tasks: list[dict]) -> None:
    by_id = {task["task_id"]: task for task in tasks}
    visiting = set()
    visited = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise TaskPlanError("Circular task dependency detected")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id]["dependencies"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in by_id:
        visit(task_id)


def validate_task_plan(tasks: list[dict]) -> None:
    if not isinstance(tasks, list) or not tasks:
        raise TaskPlanError("Task plan must contain at least one task")
    task_ids = set()
    for task in tasks:
        _validate_task_shape(task, task_ids)
        execution_type = task.get("execution_type", "implementation")
        if not isinstance(execution_type, str) or execution_type not in EXECUTION_TYPES:
            raise TaskPlanError(f"Unsupported execution type: {execution_type}")
    _assert_dependencies_exist(tasks, task_ids)
    _assert_acyclic(tasks)


def ready_task_ids(tasks: list[dict]) -> list[str]:
    validate_task_plan(tasks)
    by_id = {task["task_id"]: task for task in tasks}
    ready = []
    for task in tasks:
        if task["status"] not in {"PENDING", "READY"}:
            continue
        if all(by_id[dependency]["status"] == "COMPLETED" for dependency in task["dependencies"]):
            ready.append(task["task_id"])
    return sorted(ready)


def independent_ready_batches(tasks: list[dict]) -> list[list[str]]:
    """Group ready tasks whose allowed files do not overlap."""
    validate_task_plan(tasks)
    by_id = {task["task_id"]: task for task in tasks}
    batches = []
    for task_id in ready_task_ids(tasks):
        files = set(by_id[task_id]["allowed_files"])
        placed = False
        for batch in batches:
            batch_files = {
                path
                for current_id in batch
                for path in by_id[current_id]["allowed_files"]
            }
            if files.isdisjoint(batch_files):
                batch.append(task_id)
                placed = True
                break
        if not placed:
            batches.append([task_id])
    return batches


def topological_execution_waves(tasks: list[dict]) -> list[list[str]]:
    """Partition tasks into sequential waves of concurrent, file-disjoint tasks.

    Each wave contains tasks whose dependencies were satisfied in earlier waves.
    Within each wave, tasks are mutually independent and have disjoint allowed_files.
    """
    validate_task_plan(tasks)
    by_id = {task["task_id"]: task for task in tasks}
    completed = set()
    remaining = set(by_id.keys())
    waves = []

    while remaining:
        ready = [
            t_id for t_id in sorted(remaining)
            if all(dep in completed for dep in by_id[t_id]["dependencies"])
        ]
        if not ready:
            raise TaskPlanError("Circular or unresolvable task dependencies detected")

        wave = []
        wave_files = set()
        for t_id in ready:
            files = set(by_id[t_id]["allowed_files"])
            if wave_files.isdisjoint(files):
                wave.append(t_id)
                wave_files.update(files)

        waves.append(wave)
        completed.update(wave)
        remaining.difference_update(wave)

    return waves


def schedule_tasks(tasks: list[dict]) -> tuple[list[dict], list[str], list[list[str]]]:
    scheduled = deepcopy(tasks)
    validate_task_plan(scheduled)
    ready = ready_task_ids(scheduled)
    for task in scheduled:
        if task["task_id"] in ready:
            task["status"] = "READY"
    return scheduled, ready, independent_ready_batches(tasks)


def start_tasks(tasks: list[dict], task_ids: list[str]) -> list[dict]:
    started = deepcopy(tasks)
    validate_task_plan(started)
    selected = set(task_ids)
    for task in started:
        if task["task_id"] in selected:
            if task["status"] != "READY":
                raise TaskPlanError(f"Task is not ready: {task['task_id']}")
            task["status"] = "RUNNING"
    return started


def _set_finished_status(tasks: list[dict], task_ids: set[str], success: bool) -> None:
    for task in tasks:
        if task["task_id"] not in task_ids:
            continue
        if task["status"] != "RUNNING":
            raise TaskPlanError(f"Task is not running: {task['task_id']}")
        task["status"] = "COMPLETED" if success else "FAILED"


def _block_dependents(tasks: list[dict]) -> None:
    statuses = {task["task_id"]: task["status"] for task in tasks}
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if task["status"] != "PENDING":
                continue
            if any(statuses[dependency] in {"FAILED", "BLOCKED"} for dependency in task["dependencies"]):
                task["status"] = "BLOCKED"
                statuses[task["task_id"]] = "BLOCKED"
                changed = True


def finish_tasks(tasks: list[dict], task_ids: list[str], success: bool) -> list[dict]:
    finished = deepcopy(tasks)
    validate_task_plan(finished)
    _set_finished_status(finished, set(task_ids), success)
    if not success:
        _block_dependents(finished)
    return finished


def tasks_from_plan(plan: str, allowed_file: str, *, strict: bool = False) -> list[dict]:
    """Parse strict JSON task plans; retain a safe one-task legacy fallback."""
    import json

    try:
        payload = json.loads(plan)
        candidate = payload.get("tasks") if isinstance(payload, dict) else payload
        if isinstance(candidate, list):
            tasks = []
            for task in candidate:
                tasks.append(
                    {
                        "execution_type": task.get("execution_type", "implementation"),
                        "task_id": task["task_id"],
                        "description": task["description"],
                        "dependencies": task["dependencies"],
                        "status": "PENDING",
                        "allowed_files": task["allowed_files"],
                    }
                )
            validate_task_plan(tasks)
            return tasks
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        if strict:
            raise TaskPlanError("Planner must return a valid JSON task plan") from error

    if strict:
        raise TaskPlanError("Planner must return a JSON tasks array")

    return [
        {
            "execution_type": "implementation",
            "task_id": "implementation",
            "description": plan.strip() or "Execute the approved implementation plan",
            "dependencies": [],
            "status": "PENDING",
            "allowed_files": [allowed_file],
        }
    ]


def task_definition_hash(tasks: list[dict], *, validate: bool = True) -> str:
    """Bind approval to task definitions while allowing scheduler status changes."""
    import hashlib
    import json
    if validate:
        validate_task_plan(tasks)
    definitions = [
        {**{key: value for key, value in task.items() if key != "status"},
         "execution_type": task.get("execution_type", "implementation")}
        for task in tasks
    ]
    return hashlib.sha256(json.dumps(definitions, sort_keys=True).encode()).hexdigest()
