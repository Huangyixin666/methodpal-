"""LangGraph-backed MethodPal agent workflow.

The executor import is deliberately lazy so pure routing/intake policies remain
testable when optional backend dependencies have not been installed yet.
"""


def run_agent_graph(*args, **kwargs):
    from app.services.langgraph_agent.executor import run_agent_graph as _run_agent_graph

    return _run_agent_graph(*args, **kwargs)


__all__ = ["run_agent_graph"]
