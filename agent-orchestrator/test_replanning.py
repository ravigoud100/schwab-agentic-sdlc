import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from main import (
    EngineeringState,
    quality_gate,
    replanning_node,
    replan_review_node,
    replan_stopped_node,
    replan_approval_router,
    validation_node,
)


def _state():
    return {
        "workflow_id": "replan-test",
        "requirement": "Add expiration support",
        "plan": "Implement expiration and run Maven tests.",
        "plan_version": 1,
        "replan_history": [
            {"version": 1, "plan": "Implement expiration and run Maven tests."}
        ],
        "retry_count": 0,
        "replan_attempted": False,
        "replan_meaningful_change": True,
        "validation_result": {},
    }


def test_successful_replanning_uses_failure_context_and_versions_plan():
    state = _state()
    failed = {
        "passed": False,
        "exit_code": 1,
        "duration_seconds": 0.2,
        "output": "ShortUrl.java: missing expiresAt accessor",
    }
    response = SimpleNamespace(
        content=json.dumps(
            {
                "revised_plan": "Inspect ShortUrl accessor, apply one approved edit, then rerun Maven.",
                "change_summary": "Added an accessor check before the repair step.",
            }
        )
    )

    mocked_model = SimpleNamespace(invoke=Mock(return_value=response))
    with patch("main.validate_implementation", return_value=failed), patch(
        "main.model", mocked_model
    ), patch("main.log_event") as audit:
        validation = validation_node(state)
        replanned = replanning_node({**state, **validation})

    assert replanned["plan_version"] == 2
    assert replanned["replan_meaningful_change"] is True
    assert replanned["replan_context"]["failure_details"]["exit_code"] == 1
    assert "missing expiresAt accessor" in replanned["replan_context"][
        "failure_details"
    ]["output"]
    assert replanned["replan_history"][-1]["version"] == 2
    events = [call.kwargs["event"] for call in audit.call_args_list]
    assert "VALIDATION_FAILED" in events
    assert "REPLAN_GENERATED" in events

    approved = {**state, **replanned, "replan_approval": "approve"}
    assert replan_approval_router(approved) == "approved"


def test_replan_rejection_stops_before_repair():
    graph = StateGraph(EngineeringState)
    graph.add_node("replan_review", replan_review_node)
    graph.add_node("replan_stopped", replan_stopped_node)
    graph.set_entry_point("replan_review")
    graph.add_edge("replan_review", "replan_stopped")
    graph.add_edge("replan_stopped", END)
    app = graph.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "replan-rejection"}}
    state = {
        **_state(),
        "revised_plan": "Use a different approved repair.",
        "plan_change_summary": "Changed the repair sequence.",
        "plan_version": 2,
        "replan_meaningful_change": True,
    }

    with patch("main.log_event") as audit:
        paused = app.invoke(state, config=config)
        assert "__interrupt__" in paused
        result = app.invoke(Command(resume="reject"), config=config)

    assert result["replan_approval"] == "reject"
    assert result["status"] == "STOPPED_REPLAN_NOT_APPLIED"
    assert audit.call_args_list[0].kwargs["event"] == "REPLAN_REJECTED"


def test_unchanged_plan_is_not_reexecuted():
    state = _state()
    response = SimpleNamespace(
        content=json.dumps(
            {
                "revised_plan": "  Implement expiration and run Maven tests.  ",
                "change_summary": "No changes are required.",
            }
        )
    )

    mocked_model = SimpleNamespace(invoke=Mock(return_value=response))
    with patch("main.model", mocked_model), patch("main.log_event") as audit:
        result = replanning_node(state)

    assert result["replan_meaningful_change"] is False
    assert result["status"] == "REPLAN_UNCHANGED"
    assert replan_approval_router({**state, **result}) == "rejected"
    assert quality_gate(
        {
            "validation_result": {"passed": False},
            "retry_count": 0,
            "replan_attempted": True,
            "replan_meaningful_change": False,
        }
    ) == "failed"
    assert audit.call_args.kwargs["event"] == "REPLAN_UNCHANGED"


def test_retry_exhaustion_remains_failed():
    assert quality_gate(
        {
            "validation_result": {"passed": False},
            "retry_count": 2,
            "replan_attempted": True,
            "replan_meaningful_change": True,
        }
    ) == "failed"
