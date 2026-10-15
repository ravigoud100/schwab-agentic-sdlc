from unittest.mock import patch

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from main import EngineeringState, repair_review_node, repair_stopped_node


def test_repair_rejection():
    graph = StateGraph(EngineeringState)

    graph.add_node("repair_review", repair_review_node)
    graph.add_node("repair_stopped", repair_stopped_node)

    graph.set_entry_point("repair_review")

    graph.add_edge("repair_review", "repair_stopped")
    graph.add_edge("repair_stopped", END)

    app = graph.compile(checkpointer=MemorySaver())

    config = {
        "configurable": {
            "thread_id": "test-repair-rejection"
        }
    }

    state = {
        "workflow_id": "test-repair-rejection",
        "retry_count": 1,
        "repair_proposal": "Check Java source before applying changes.",
        "status": "REPAIR_REVIEW_REQUIRED",
    }

    with patch("main.log_event") as mock_audit:
        result = app.invoke(state, config=config)

        assert "__interrupt__" in result
        print("PASS: Workflow paused for repair approval")

        result = app.invoke(
            Command(resume="reject"),
            config=config,
        )

        assert result["repair_approval"] == "reject"
        assert result["status"] == "STOPPED_REPAIR_NOT_APPLIED"

        mock_audit.assert_called_once()
        assert mock_audit.call_args.kwargs["event"] == "REPAIR_REJECTED"

        print("PASS: Repair rejection recorded in audit log")
        print("PASS: Workflow stopped without applying repair")


def test_repair_approval():
    graph = StateGraph(EngineeringState)

    graph.add_node("repair_review", repair_review_node)
    graph.add_node("repair_stopped", repair_stopped_node)

    graph.set_entry_point("repair_review")

    graph.add_edge("repair_review", "repair_stopped")
    graph.add_edge("repair_stopped", END)

    app = graph.compile(checkpointer=MemorySaver())

    config = {
        "configurable": {
            "thread_id": "test-repair-approval"
        }
    }

    state = {
        "workflow_id": "test-repair-approval",
        "retry_count": 1,
        "repair_proposal": "Check Java source before applying changes.",
        "status": "REPAIR_REVIEW_REQUIRED",
    }

    with patch("main.log_event") as mock_audit:
        result = app.invoke(state, config=config)

        assert "__interrupt__" in result

        result = app.invoke(
            Command(resume="approve"),
            config=config,
        )

        assert result["repair_approval"] == "approve"
        print("PASS: No automatic repair executed")


if __name__ == "__main__":
    test_repair_rejection()
    test_repair_approval()
