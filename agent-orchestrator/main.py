import os
from typing import TypedDict

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END

load_dotenv()

model = ChatGoogleGenerativeAI(
    model="gemini-3.8-flash",
    temperature=0
)


class EngineeringState(TypedDict):
    requirement: str
    analysis: str
    tasks: list[str]
    status: str


def requirements_agent(state: EngineeringState):
    print("\n[REQUIREMENTS AGENT] Analyzing requirement using Gemini...")

    prompt = f"""
    You are a senior software requirements engineer.

    Analyze the following requirement for an existing
    Java Spring Boot URL shortener.

    Requirement:
    {state["requirement"]}

    Provide:
    1. Functional requirements
    2. Non-functional requirements
    3. Ambiguities
    4. Acceptance criteria
    5. Risks and assumptions

    Be specific and concise.
    """

    response = model.invoke(prompt)

    return {
        "analysis": response.text,
        "status": "REQUIREMENTS_ANALYZED"
    }


def planning_agent(state: EngineeringState):
    print("\n[PLANNING AGENT] Generating engineering plan using Gemini...")

    prompt = f"""
    You are a senior software architect.

    Based on the requirement analysis below, create
    an actionable implementation plan for a Java
    Spring Boot URL shortener.

    Analysis:
    {state["analysis"]}

    Produce exactly six numbered development tasks.
    Include relevant Java components, testing,
    and validation activities.

    Return only the numbered tasks.
    """

    response = model.invoke(prompt)

    tasks = [
        line.strip()
        for line in response.text.splitlines()
        if line.strip()
    ]

    return {
        "tasks": tasks,
        "status": "PLAN_CREATED"
    }


workflow = StateGraph(EngineeringState)

workflow.add_node("requirements", requirements_agent)
workflow.add_node("planning", planning_agent)

workflow.add_edge(START, "requirements")
workflow.add_edge("requirements", "planning")
workflow.add_edge("planning", END)

app = workflow.compile()


if __name__ == "__main__":
    print("\nSCHWAB AGENTIC SOFTWARE ENGINEERING SYSTEM")
    print("-----------------------------------------")

    requirement = input("\nEnter engineering requirement: ")

    initial_state = {
        "requirement": requirement,
        "analysis": "",
        "tasks": [],
        "status": "STARTED"
    }

    result = app.invoke(initial_state)

    print("\n========== ENGINEERING OUTPUT ==========")

    print("\nRequirement:")
    print(result["requirement"])

    print("\nRequirements Analysis:")
    print(result["analysis"])

    print("\nDevelopment Plan:")
    for task in result["tasks"]:
        print(task)

    print("\nWorkflow Status:", result["status"])