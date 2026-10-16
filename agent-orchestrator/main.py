import json
import uuid
from pathlib import Path
from typing import TypedDict

from governance.workspace_manager import initialize_workspace
from governance.audit_logger import log_event
from agents.implementation_agent import implement_expiration
from agents.repair_agent import parse_repair_proposal, propose_repair
from agents.validation_agent import validate_implementation
from governance.rollback_manager import (
    create_checkpoint,
    rollback,
    file_hash,
)
from governance.repair_executor import apply_approved_repair
from governance.task_orchestrator import (
    TaskPlanError,
    finish_tasks,
    schedule_tasks,
    start_tasks,
    tasks_from_plan,
    validate_task_plan,
)

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.codebase_agent import analyze_codebase

load_dotenv()

IMPLEMENTATION_RELATIVE_PATH = (
    "url-shortener/src/main/java/url_shortener/ShortUrl.java"
)

model = ChatOllama(
    model="qwen2.5-coder:latest",
    temperature=0
)



# Information shared between agents
class EngineeringState(TypedDict):
    requirement: str
    analysis: str
    codebase_analysis: str
    plan: str
    approval: str
    status: str
    implementation_file: str
    validation_result: dict
    repair_proposal: str
    repair_approval: str
    retry_count: int
    rollback_checkpoint: dict
    implementation_changed: bool
    expected_current_hash: str
    workflow_id: str
    workspace: str
    repair_change: dict
    repair_checkpoint: dict
    repair_expected_current_hash: str
    repair_applied: bool
    repair_rolled_back: bool
    repair_rollback_blocked: bool
    plan_version: int
    revised_plan: str
    plan_change_summary: str
    replan_context: dict
    replan_history: list
    replan_approval: str
    replan_meaningful_change: bool
    replan_attempted: bool
    revised_tasks: list
    tasks: list
    active_task_ids: list
    task_plan_valid: bool
    task_parallel_batches: list


# Agent 1: Analyze requirements
def requirements_agent(state: EngineeringState):
    print("\n[1] REQUIREMENTS AGENT")

    response = model.invoke(
        f"""
        You are a senior requirements engineer.

        Analyze this requirement for a Java URL shortener:

        {state["requirement"]}

        Identify:
        - Functional requirements
        - Nonfunctional requirements
        - Ambiguities
        - Acceptance criteria
        - Risks and assumptions

        Do not invent missing requirements.
        """
    )

    return {
        "analysis": response.text,
        "status": "REQUIREMENTS_ANALYZED"
    }


# Agent 2: Analyze actual Java code
def codebase_agent(state: EngineeringState):
    print("\n[2] CODEBASE ANALYSIS AGENT")

    result = analyze_codebase(state["requirement"], Path(state["workspace"]))

    return {
        "codebase_analysis": result,
        "status": "CODEBASE_ANALYZED"
    }


# Agent 3: Generate an implementation plan
def planning_agent(state: EngineeringState):
    print("\n[3] PLANNING AGENT")

    response = model.invoke(
        f"""
        You are a senior Java software architect.

        Requirement analysis:
        {state["analysis"]}

        Existing codebase analysis:
        {state["codebase_analysis"]}

        Create an implementation plan.

        Include:
        1. Files to modify
        2. New files required
        3. Task dependencies
        4. Testing approach
        5. Risks and approval requirements

        Do not invent existing Java components.
        """
    )

    plan = response.text
    tasks = tasks_from_plan(
        plan,
        IMPLEMENTATION_RELATIVE_PATH,
    )
    return {
        "plan": plan,
        "plan_version": 1,
        "replan_history": [
            {"version": 1, "plan": plan, "source": "initial"}
        ],
        "tasks": tasks,
        "status": "PLAN_CREATED"
    }


def _response_text(response) -> str:
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", response)
    return content if isinstance(content, str) else str(content)


def _parse_replan_response(response) -> tuple[str, str]:
    content = _response_text(response).strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("Replanning response must be valid JSON") from error

    if not isinstance(payload, dict):
        raise ValueError("Replanning response must be a JSON object")
    if set(payload) != {"revised_plan", "change_summary"}:
        raise ValueError(
            "Replanning response requires revised_plan and change_summary"
        )
    revised_plan = payload["revised_plan"]
    change_summary = payload["change_summary"]
    if not isinstance(revised_plan, str) or not revised_plan.strip():
        raise ValueError("Revised plan must be non-empty text")
    if not isinstance(change_summary, str) or not change_summary.strip():
        raise ValueError("Plan change summary must be non-empty text")
    return revised_plan.strip(), change_summary.strip()


def replanning_node(state: EngineeringState):
    print("\n[REPLANNING AGENT]")
    failure = state.get("validation_result", {})
    retry_history = state.get("replan_history", [])
    context = {
        "original_requirement": state["requirement"],
        "current_plan": state["plan"],
        "failure_details": {
            "passed": failure.get("passed", False),
            "exit_code": failure.get("exit_code"),
            "duration_seconds": failure.get("duration_seconds"),
            "output": failure.get("output", ""),
        },
        "retry_history": retry_history,
    }
    prompt = f"""
You are revising a Java 21 Spring Boot 4 engineering plan after validation failed.
Do not execute commands, change files, change Java versions, or weaken validation.
Return only valid JSON with exactly these string fields:
{{"revised_plan": "...", "change_summary": "..."}}

Context:
{json.dumps(context, indent=2)}
"""

    try:
        revised_plan, change_summary = _parse_replan_response(model.invoke(prompt))
    except Exception as error:
        log_event(
            workflow_id=state["workflow_id"],
            agent="replanning_agent",
            event="REPLAN_BLOCKED",
            details={"error_type": type(error).__name__},
        )
        return {
            "replan_attempted": True,
            "replan_meaningful_change": False,
            "status": "REPLAN_BLOCKED",
        }

    meaningful = " ".join(revised_plan.split()) != " ".join(
        state["plan"].split()
    )
    revised_tasks = tasks_from_plan(
        revised_plan,
        IMPLEMENTATION_RELATIVE_PATH,
    )
    version = state.get("plan_version", 0) + 1
    history_entry = {
        "version": version,
        "plan": revised_plan,
        "change_summary": change_summary,
        "source": "replan",
    }
    log_event(
        workflow_id=state["workflow_id"],
        agent="replanning_agent",
        event="REPLAN_GENERATED" if meaningful else "REPLAN_UNCHANGED",
        details={
            "plan_version": version,
            "meaningful_change": meaningful,
            "retry_count": state.get("retry_count", 0),
        },
    )
    return {
        "revised_plan": revised_plan,
        "plan_change_summary": change_summary,
        "plan_version": version,
        "replan_context": context,
        "replan_history": retry_history + [history_entry],
        "revised_tasks": revised_tasks,
        "replan_meaningful_change": meaningful,
        "replan_attempted": True,
        "replan_approval": "",
        "status": "REPLAN_REVIEW_REQUIRED" if meaningful else "REPLAN_UNCHANGED",
    }


def replan_review_node(state: EngineeringState):
    if not state.get("replan_meaningful_change"):
        return {"replan_approval": "reject", "status": "REPLAN_UNCHANGED"}

    print("\n[REPLAN REVIEW] Human approval required")
    decision = interrupt({
        "type": "REPLAN_APPROVAL",
        "plan_version": state["plan_version"],
        "revised_plan": state["revised_plan"],
        "change_summary": state["plan_change_summary"],
        "message": "Approve or reject the revised engineering plan.",
    })
    approved = str(decision).strip().lower() == "approve"
    log_event(
        workflow_id=state["workflow_id"],
        agent="human_replan_review",
        event="REPLAN_APPROVED" if approved else "REPLAN_REJECTED",
        details={"plan_version": state["plan_version"]},
    )
    updates: dict[str, object] = {
        "replan_approval": "approve" if approved else "reject",
        "status": "REPLAN_APPROVED" if approved else "REPLAN_REJECTED",
    }
    if approved:
        try:
            scheduled, ready, parallel_batches = schedule_tasks(
                state["revised_tasks"]
            )
        except TaskPlanError as error:
            log_event(
                workflow_id=state["workflow_id"],
                agent="task_scheduler",
                event="TASK_SCHEDULING_FAILED",
                details={"error_type": type(error).__name__},
            )
            return {
                "replan_approval": "reject",
                "status": "STOPPED_INVALID_TASK_PLAN",
            }
        updates["plan"] = state["revised_plan"]
        updates["tasks"] = scheduled
        updates["active_task_ids"] = ready[:1]
        updates["task_parallel_batches"] = parallel_batches
        updates["task_plan_valid"] = bool(ready)
    return updates


def replan_approval_router(state: EngineeringState):
    if (
        state.get("replan_approval") == "approve"
        and state.get("replan_meaningful_change")
        and state.get("task_plan_valid", True)
        and state.get("active_task_ids")
    ):
        return "approved"
    return "rejected"


def replan_stopped_node(state: EngineeringState):
    log_event(
        workflow_id=state["workflow_id"],
        agent="replanning_agent",
        event="REPLAN_STOPPED",
        details={
            "plan_version": state.get("plan_version", 0),
            "meaningful_change": state.get("replan_meaningful_change", False),
        },
    )
    return {"status": "STOPPED_REPLAN_NOT_APPLIED"}


# Human approval checkpoint
def human_approval(state: EngineeringState):
    print("\n[4] HUMAN APPROVAL REQUIRED")

    decision = interrupt({
        "message": "Review the engineering plan",
        "requirement": state["requirement"],
        "plan": state["plan"],
        "allowed_decisions": ["approve", "reject"]
    })

    approved = decision == "approve"

    return {
        "approval": decision,
        "status": "APPROVED" if approved else "REJECTED"
    }


# Decide which path to follow
def approval_router(state: EngineeringState):
    if state["approval"] == "approve":
        return "approved"

    return "rejected"


def approved_node(state: EngineeringState):
    print("\n[5] PLAN APPROVED")
    log_event(
        workflow_id=state["workflow_id"],
        agent="human_approval",
        event="PLAN_APPROVED",
        details={"decision": "approve"},
    )

    return {
        "status": "READY_FOR_IMPLEMENTATION"
    }


def task_scheduler_node(state: EngineeringState):
    print("\n[TASK SCHEDULER]")
    if state.get("approval") != "approve":
        return {"task_plan_valid": False, "status": "STOPPED"}

    try:
        scheduled, ready, parallel_batches = schedule_tasks(state["tasks"])
    except TaskPlanError as error:
        log_event(
            workflow_id=state["workflow_id"],
            agent="task_scheduler",
            event="TASK_SCHEDULING_FAILED",
            details={"error_type": type(error).__name__},
        )
        return {
            "task_plan_valid": False,
            "status": "STOPPED_INVALID_TASK_PLAN",
        }

    if not ready:
        log_event(
            workflow_id=state["workflow_id"],
            agent="task_scheduler",
            event="TASK_SCHEDULING_FAILED",
            details={"error_type": "NoEligibleTasks"},
        )
        return {
            "task_plan_valid": False,
            "status": "STOPPED_NO_ELIGIBLE_TASKS",
        }

    active = [ready[0]]
    log_event(
        workflow_id=state["workflow_id"],
        agent="task_scheduler",
        event="TASKS_SCHEDULED",
        details={
            "ready_task_ids": ready,
            "active_task_ids": active,
            "parallel_batches": parallel_batches,
        },
    )
    return {
        "tasks": scheduled,
        "active_task_ids": active,
        "task_parallel_batches": parallel_batches,
        "task_plan_valid": True,
        "status": "TASKS_READY",
    }


def task_scheduler_router(state: EngineeringState):
    return "implementation" if state.get("task_plan_valid") else "failure"


def implementation_node(state: EngineeringState):
    print("\n[IMPLEMENTATION AGENT]")

    if (
        state["approval"] != "approve"
        or not state.get("task_plan_valid")
        or not state.get("active_task_ids")
    ):
        return {"status": "STOPPED"}

    try:
        running_tasks = start_tasks(state["tasks"], state["active_task_ids"])
        log_event(
            workflow_id=state["workflow_id"],
            agent="task_scheduler",
            event="TASKS_STARTED",
            details={"task_ids": state["active_task_ids"]},
        )
    except TaskPlanError as error:
        log_event(
            workflow_id=state["workflow_id"],
            agent="task_scheduler",
            event="TASK_EXECUTION_FAILED",
            details={"error_type": type(error).__name__},
        )
        return {"task_plan_valid": False, "status": "STOPPED_TASK_NOT_READY"}

    relative_path = (
        IMPLEMENTATION_RELATIVE_PATH
    )
    checkpoint = create_checkpoint(relative_path, Path(state["workspace"]))
    modified_file = implement_expiration(approved=True, workspace=Path(state["workspace"]))
    current_hash = file_hash(
        Path(modified_file).read_bytes()
    )
    changed = current_hash != checkpoint["original_hash"]
    print(f"[IMPLEMENTATION] File changed: {changed}")
    log_event(
        workflow_id=state["workflow_id"],
        agent="implementation_agent",
        event="IMPLEMENTATION_COMPLETED",
        details={"file_changed": changed},
    )

    return {
        "implementation_file": str(modified_file),
        "rollback_checkpoint": checkpoint,
        "implementation_changed": changed,
        "expected_current_hash": current_hash,
        "tasks": running_tasks,
        "status": "IMPLEMENTATION_COMPLETED",
    }


def validation_node(state: EngineeringState):
    print("\n[VALIDATION AGENT]")

    result = validate_implementation(Path(state["workspace"]))
    log_event(
        workflow_id=state["workflow_id"],
        agent="validation_agent",
        event="VALIDATION_PASSED" if result["passed"] else "VALIDATION_FAILED",
        details={
            "exit_code": result["exit_code"],
            "duration_seconds": result["duration_seconds"],
            "retry_count": state.get("retry_count", 0),
            "repair_applied": state.get("repair_applied", False),
        },
    )

    updates: dict[str, object] = {
        "validation_result": result,
        "status": "VALIDATION_PASSED"
        if result["passed"]
        else "VALIDATION_FAILED"
    }

    if state.get("active_task_ids") and state.get("tasks"):
        try:
            updates["tasks"] = finish_tasks(
                state["tasks"],
                state["active_task_ids"],
                result["passed"],
            )
            log_event(
                workflow_id=state["workflow_id"],
                agent="task_scheduler",
                event="TASKS_COMPLETED" if result["passed"] else "TASKS_FAILED",
                details={"task_ids": state["active_task_ids"]},
            )
        except TaskPlanError as error:
            log_event(
                workflow_id=state["workflow_id"],
                agent="task_scheduler",
                event="TASK_EXECUTION_FAILED",
                details={"error_type": type(error).__name__},
            )
            updates["task_plan_valid"] = False

    if (
        not result["passed"]
        and state.get("repair_applied")
        and not state.get("repair_rolled_back")
    ):
        try:
            rollback(
                state["repair_checkpoint"],
                state["repair_expected_current_hash"],
                Path(state["workspace"]),
            )
            log_event(
                workflow_id=state["workflow_id"],
                agent="rollback_manager",
                event="REPAIR_ROLLBACK_COMPLETED",
                details={"path": state["repair_checkpoint"]["path"]},
            )
            updates.update(
                {
                    "repair_applied": False,
                    "repair_rolled_back": True,
                    "repair_rollback_blocked": False,
                }
            )
        except Exception as error:
            log_event(
                workflow_id=state["workflow_id"],
                agent="rollback_manager",
                event="REPAIR_ROLLBACK_BLOCKED",
                details={"error_type": type(error).__name__},
            )
            updates["repair_rollback_blocked"] = True

    return updates


MAX_RETRIES = 2



def retry_node(state: EngineeringState):
    retry_count = state.get("retry_count", 0) + 1
    print(f"\n[RETRY] Attempt {retry_count}/{MAX_RETRIES}")
    error_log = state["validation_result"].get("output", "")
    if error_log.strip():
        proposal = propose_repair(error_log, Path(state["workspace"]))
    else:
        proposal = "Insufficient validation evidence. No repair proposed."
    repair_change = parse_repair_proposal(proposal)
    log_event(
        workflow_id=state["workflow_id"],
        agent="repair_agent",
        event="REPAIR_PROPOSED",
        details={
            "retry_count": retry_count,
            "proposal_available": bool(proposal.strip()),
            "repair_change_available": repair_change is not None,
        },
    )

    return {
        "retry_count": retry_count,
        "repair_proposal": proposal,
        "repair_change": repair_change or {},
        "status": "REPAIR_REVIEW_REQUIRED",
    }


def repair_review_node(state: EngineeringState):
    from langgraph.types import interrupt

    print("\n[REPAIR REVIEW] Human approval required")
    print("Proposed repair:\n", state["repair_proposal"])
    decision = interrupt({
        "type": "REPAIR_APPROVAL",
        "proposal": state["repair_proposal"],
        "retry_count": state["retry_count"],
        "message": "Approve or reject the proposed repair.",
    })
    approved = str(decision).strip().lower() == "approve"
    log_event(
        workflow_id=state["workflow_id"],
        agent="human_repair_review",
        event="REPAIR_APPROVED" if approved else "REPAIR_REJECTED",
        details={"retry_count": state["retry_count"]},
    )
    return {
        "repair_approval": "approve" if approved else "reject"
    }


def repair_approval_router(state: EngineeringState):
    if state.get("repair_approval") == "approve" and state.get("repair_change"):
        return "approved"
    return "rejected"


def repair_executor_node(state: EngineeringState):
    print("\n[REPAIR EXECUTOR]")
    if state.get("repair_approval") != "approve":
        return {"status": "STOPPED_REPAIR_NOT_APPLIED"}

    change = state.get("repair_change") or {}
    try:
        running_tasks = None
        if state.get("tasks"):
            running_tasks = start_tasks(
                state["tasks"], state.get("active_task_ids", [])
            )
        result = apply_approved_repair(change, Path(state["workspace"]))
    except Exception as error:
        log_event(
            workflow_id=state["workflow_id"],
            agent="repair_executor",
            event="REPAIR_EXECUTION_BLOCKED",
            details={"error_type": type(error).__name__},
        )
        return {"status": "STOPPED_REPAIR_NOT_APPLIED"}

    log_event(
        workflow_id=state["workflow_id"],
        agent="repair_executor",
        event="REPAIR_EXECUTED",
        details={"path": result["path"], "file_changed": result["changed"]},
    )
    updates: dict[str, object] = {
        "repair_checkpoint": result["checkpoint"],
        "repair_expected_current_hash": result["expected_current_hash"],
        "repair_applied": True,
        "repair_rolled_back": False,
        "repair_rollback_blocked": False,
        "status": "REPAIR_APPLIED",
    }
    if running_tasks is not None:
        updates["tasks"] = running_tasks
    return updates


def repair_execution_router(state: EngineeringState):
    return "validation" if state.get("repair_applied") else "stopped"


def repair_stopped_node(state: EngineeringState):
    print("\n[REPAIR] Awaiting separate implementation authorization.")
    return {"status": "STOPPED_REPAIR_NOT_APPLIED"}


def quality_gate(state: EngineeringState):
    if state["validation_result"]["passed"]:
        if any(
            task["status"] in {"PENDING", "READY", "RUNNING"}
            for task in state.get("tasks", [])
        ):
            return "tasks"
        return "passed"

    retry_count = state.get("retry_count", 0)
    if state.get("repair_rollback_blocked"):
        return "failed"
    if state.get("replan_attempted") and not state.get("replan_meaningful_change"):
        return "failed"
    if retry_count < MAX_RETRIES:
        return "replan"

    return "failed"


def success_node(state: EngineeringState):
    print("\n[QUALITY GATE] Validation passed")
    log_event(
        workflow_id=state["workflow_id"],
        agent="quality_gate",
        event="AWAITING_FINAL_HUMAN_REVIEW",
        details={},
    )

    return {
        "status": "AWAITING_FINAL_HUMAN_REVIEW"
    }


def failure_node(state: EngineeringState):
    print("\n[QUALITY GATE] Validation failed")

    if state.get("repair_rollback_blocked"):
        print("[ROLLBACK] Repair rollback requires review.")
        return {
            "status": "STOPPED_ROLLBACK_REQUIRES_REVIEW"
        }

    if not state.get("implementation_changed", False):
        print("[ROLLBACK] No new AI changes. Nothing to restore.")
        log_event(
            workflow_id=state["workflow_id"],
            agent="rollback_manager",
            event="VALIDATION_STOPPED",
            details={"rollback_needed": False},
        )
        return {
            "status": "STOPPED_VALIDATION_FAILED"
        }

    checkpoint = state["rollback_checkpoint"]
    expected_hash = state["expected_current_hash"]
    try:
        rollback(checkpoint, expected_hash, Path(state["workspace"]))
        print("[ROLLBACK] AI-generated changes restored.")
        log_event(
            workflow_id=state["workflow_id"],
            agent="rollback_manager",
            event="ROLLBACK_COMPLETED",
            details={"rollback_needed": True},
        )
        return {
            "status": "STOPPED_ROLLED_BACK"
        }
    except Exception as error:
        print(f"[ROLLBACK] Failed safely: {error}")
        log_event(
            workflow_id=state["workflow_id"],
            agent="rollback_manager",
            event="ROLLBACK_BLOCKED",
            details={"error_type": type(error).__name__},
        )
        return {
            "status": "STOPPED_ROLLBACK_REQUIRES_REVIEW"
        }


def rejected_node(state: EngineeringState):
    print("\n[5] PLAN REJECTED — SAFE STOP")
    log_event(
        workflow_id=state["workflow_id"],
        agent="human_approval",
        event="PLAN_REJECTED",
        details={"decision": "reject"},
    )

    return {
        "status": "STOPPED"
    }


# Build LangGraph workflow
workflow = StateGraph(EngineeringState)

workflow.add_node("requirements", requirements_agent)
workflow.add_node("codebase", codebase_agent)
workflow.add_node("planning", planning_agent)
workflow.add_node("task_scheduler", task_scheduler_node)
workflow.add_node("replanning", replanning_node)
workflow.add_node("replan_review", replan_review_node)
workflow.add_node("replan_stopped", replan_stopped_node)
workflow.add_node("approval", human_approval)
workflow.add_node("approved", approved_node)
workflow.add_node("rejected", rejected_node)
workflow.add_node("implementation", implementation_node)
workflow.add_node("validation", validation_node)
workflow.add_node("retry", retry_node)
workflow.add_node("repair_review", repair_review_node)
workflow.add_node("repair_executor", repair_executor_node)
workflow.add_node("repair_stopped", repair_stopped_node)
workflow.add_node("success", success_node)
workflow.add_node("failure", failure_node)

workflow.add_edge(START, "requirements")
workflow.add_edge("requirements", "codebase")
workflow.add_edge("codebase", "planning")
workflow.add_edge("planning", "approval")

workflow.add_conditional_edges(
    "approval",
    approval_router,
    {
        "approved": "approved",
        "rejected": "rejected"
    }
)

workflow.add_edge("approved", "task_scheduler")
workflow.add_conditional_edges(
    "task_scheduler",
    task_scheduler_router,
    {"implementation": "implementation", "failure": "failure"},
)
workflow.add_edge("rejected", END)
workflow.add_edge("implementation", "validation")

workflow.add_conditional_edges(
    "validation",
    quality_gate,
    {
        "passed": "success",
        "tasks": "task_scheduler",
        "replan": "replanning",
        "failed": "failure",
    },
)

workflow.add_edge("success", END)
workflow.add_edge("failure", END)
workflow.add_edge("replanning", "replan_review")
workflow.add_conditional_edges(
    "replan_review",
    replan_approval_router,
    {"approved": "retry", "rejected": "replan_stopped"},
)
workflow.add_edge("retry", "repair_review")
workflow.add_conditional_edges(
    "repair_review",
    repair_approval_router,
    {"approved": "repair_executor", "rejected": "repair_stopped"},
)
workflow.add_conditional_edges(
    "repair_executor",
    repair_execution_router,
    {"validation": "validation", "stopped": "repair_stopped"},
)
workflow.add_edge("repair_stopped", END)


def main():
    print("\nSCHWAB AGENTIC SDLC SYSTEM")
    print("==========================")

    requirement = input("\nEnter requirement: ").strip()

    if not requirement:
        print("Requirement cannot be empty.")
        return

    workflow_id = str(uuid.uuid4())

    workspace = initialize_workspace(workflow_id)

    config = {
        "configurable": {
            "thread_id": workflow_id
        }
    }

    # Persist checkpoints in SQLite
    with SqliteSaver.from_conn_string("workflow.db") as checkpointer:

        app = workflow.compile(checkpointer=checkpointer)

        initial_state = {
            "workflow_id": workflow_id,
            "workspace": str(workspace),
            "requirement": requirement,
            "analysis": "",
            "codebase_analysis": "",
            "plan": "",
            "approval": "",
            "status": "STARTED",
            "implementation_file": "",
            "validation_result": {},
            "repair_proposal": "",
            "repair_approval": "",
            "retry_count": 0,
            "rollback_checkpoint": {},
            "implementation_changed": False,
            "expected_current_hash": "",
            "repair_change": {},
            "repair_checkpoint": {},
            "repair_expected_current_hash": "",
            "repair_applied": False,
            "repair_rolled_back": False,
            "repair_rollback_blocked": False,
            "plan_version": 0,
            "revised_plan": "",
            "plan_change_summary": "",
            "replan_context": {},
            "replan_history": [],
            "replan_approval": "",
            "replan_meaningful_change": True,
            "replan_attempted": False,
            "revised_tasks": [],
            "tasks": [],
            "active_task_ids": [],
            "task_plan_valid": False,
            "task_parallel_batches": [],
        }

        result = app.invoke(initial_state, config=config)

        # Handle both plan approval and repair approval interrupts.
        while "__interrupt__" in result:
            interrupts = result["__interrupt__"]

            if not interrupts:
                break

            payload = interrupts[0].value

            if isinstance(payload, dict) and payload.get("type") == "REPLAN_APPROVAL":
                print("\n========== REPLAN APPROVAL ==========")
                print("Plan version:", payload.get("plan_version", 0))
                print("Changes:", payload.get("change_summary", ""))
                print("Revised plan:", payload.get("revised_plan", ""))
                print("\n1. Approve revised plan")
                print("2. Reject revised plan")
            elif isinstance(payload, dict) and payload.get("type") == "REPAIR_APPROVAL":
                print("\n========== REPAIR APPROVAL ==========")
                print("Proposal:", payload.get("proposal", ""))
                print("Retry:", payload.get("retry_count", 0))
                print("\n1. Approve proposal")
                print("2. Reject proposal")
            else:
                print("\n========== PLAN APPROVAL ==========")
                print("\n1. Approve")
                print("2. Reject")

            choice = input("\nEnter decision (1/2): ").strip()

            # Reject unexpected input rather than treating it as approval.
            decision = "approve" if choice == "1" else "reject"

            result = app.invoke(
                Command(resume=decision),
                config=config,
            )

        print("\n========== FINAL RESULT ==========")
        print("Workflow ID:", workflow_id)
        print("Requirement:", result["requirement"])
        print("Approval:", result["approval"])
        print("Status:", result["status"])


if __name__ == "__main__":
    main()
