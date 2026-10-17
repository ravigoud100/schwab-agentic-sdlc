import uuid
from typing import TypedDict

from agents.implementation_agent import implement_expiration
from agents.validation_agent import validate_implementation

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.codebase_agent import analyze_codebase

load_dotenv()

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
    retry_count: int


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

    result = analyze_codebase(state["requirement"])

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

    return {
        "plan": response.text,
        "status": "PLAN_CREATED"
    }


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

    return {
        "status": "READY_FOR_IMPLEMENTATION"
    }


def implementation_node(state: EngineeringState):
    print("\n[IMPLEMENTATION AGENT]")

    if state["approval"] != "approve":
        return {"status": "STOPPED"}

    modified_file = implement_expiration(
        approved=True
    )

    return {
        "implementation_file": str(modified_file),
        "status": "IMPLEMENTATION_COMPLETED"
    }


def validation_node(state: EngineeringState):
    print("\n[VALIDATION AGENT]")

    result = validate_implementation()

    return {
        "validation_result": result,
        "status": "VALIDATION_PASSED"
        if result["passed"]
        else "VALIDATION_FAILED"
    }


def quality_gate(state: EngineeringState):
    if state["validation_result"]["passed"]:
        return "passed"

    return "failed"


def success_node(state: EngineeringState):
    print("\n[QUALITY GATE] Validation passed")

    return {
        "status": "AWAITING_FINAL_HUMAN_REVIEW"
    }


def failure_node(state: EngineeringState):
    print("\n[QUALITY GATE] Validation failed")

    return {
        "status": "STOPPED_VALIDATION_FAILED"
    }


def rejected_node(state: EngineeringState):
    print("\n[5] PLAN REJECTED — SAFE STOP")

    return {
        "status": "STOPPED"
    }


# Build LangGraph workflow
workflow = StateGraph(EngineeringState)

workflow.add_node("requirements", requirements_agent)
workflow.add_node("codebase", codebase_agent)
workflow.add_node("planning", planning_agent)
workflow.add_node("approval", human_approval)
workflow.add_node("approved", approved_node)
workflow.add_node("rejected", rejected_node)
workflow.add_node("implementation", implementation_node)
workflow.add_node("validation", validation_node)
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

workflow.add_edge("approved", "implementation")
workflow.add_edge("rejected", END)
workflow.add_edge("implementation", "validation")

workflow.add_conditional_edges(
    "validation",
    quality_gate,
    {
        "passed": "success",
        "failed": "failure",
    },
)

workflow.add_edge("success", END)
workflow.add_edge("failure", END)


def main():
    print("\nSCHWAB AGENTIC SDLC SYSTEM")
    print("==========================")

    requirement = input("\nEnter requirement: ").strip()

    if not requirement:
        print("Requirement cannot be empty.")
        return

    workflow_id = str(uuid.uuid4())

    config = {
        "configurable": {
            "thread_id": workflow_id
        }
    }

    # Persist checkpoints in SQLite
    with SqliteSaver.from_conn_string("workflow.db") as checkpointer:

        app = workflow.compile(checkpointer=checkpointer)

        initial_state = {
            "requirement": requirement,
            "analysis": "",
            "codebase_analysis": "",
            "plan": "",
            "approval": "",
            "status": "STARTED",
            "implementation_file": "",
            "validation_result": {},
            "retry_count": 0
        }

        result = app.invoke(initial_state, config=config)

        if "__interrupt__" in result:
            print("\n========== ENGINEERING PLAN ==========")
            print(result["plan"])

            print("\nHuman approval required.")
            print("1. Approve")
            print("2. Reject")

            choice = input("\nEnter decision (1/2): ").strip()

            decision = "approve" if choice == "1" else "reject"

            result = app.invoke(
                Command(resume=decision),
                config=config
            )

        print("\n========== FINAL RESULT ==========")
        print("Workflow ID:", workflow_id)
        print("Requirement:", result["requirement"])
        print("Approval:", result["approval"])
        print("Status:", result["status"])


if __name__ == "__main__":
    main()
