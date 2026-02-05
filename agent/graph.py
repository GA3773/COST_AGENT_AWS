"""LangGraph workflow definition for the EMR cost optimization agent."""

from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from agent.nodes import (
    backup_node,
    call_agent,
    create_node,
    initialize_node,
    modify_node,
    report_node,
    revert_node,
    route_agent,
    # wait_node removed - background monitor handles waiting and revert
)
from agent.prompts import SYSTEM_PROMPT
from agent.state import AgentState
from services.azure_openai import create_llm
from tools import ALL_TOOLS


def build_graph():
    """Build and compile the LangGraph agent workflow.

    Returns:
        Compiled graph with MemorySaver checkpointer for interrupt/resume.
    """
    # Create LLM with hybrid auth (Service Principal + API key)
    llm = create_llm()

    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(ALL_TOOLS)

    # Create the agent node (closure over llm_with_tools)
    def agent_node(state: dict) -> dict:
        # Inject system prompt if not already present
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
            state = {**state, "messages": messages}
        return call_agent(state, llm_with_tools)

    # Build graph
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("initialize", initialize_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(ALL_TOOLS))
    graph.add_node("backup", backup_node)
    graph.add_node("modify", modify_node)
    graph.add_node("create", create_node)
    # wait_node removed - background monitor handles waiting
    graph.add_node("revert", revert_node)  # Only used for error paths
    graph.add_node("report", report_node)

    # Set entry point
    graph.set_entry_point("initialize")

    # Edges
    graph.add_edge("initialize", "agent")

    # Agent routing
    graph.add_conditional_edges("agent", route_agent, {
        "tools": "tools",
        "backup": "backup",
        "end": END,
    })

    # Tools loop back to agent
    graph.add_edge("tools", "agent")

    # Execution pipeline (async flow):
    # backup -> modify -> create -> report -> END
    # The create_node starts a background monitor that handles waiting and revert.
    # Revert node is only used for error paths (modify fails before Lambda is called).
    graph.add_edge("backup", "modify")

    # Modify can error -> jump to revert, otherwise create
    def modify_route(state: dict) -> str:
        return "revert" if state.get("error") else "create"
    graph.add_conditional_edges("modify", modify_route, {
        "create": "create",
        "revert": "revert",
    })

    # Create: on success -> report (background monitor handles revert)
    #         on error -> revert (need to restore config immediately)
    def create_route(state: dict) -> str:
        return "revert" if state.get("error") else "report"
    graph.add_conditional_edges("create", create_route, {
        "report": "report",
        "revert": "revert",
    })

    # Revert (error path only) -> report
    graph.add_edge("revert", "report")
    graph.add_edge("report", END)

    # Compile with checkpointer and interrupt before backup (approval gate)
    checkpointer = MemorySaver()
    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["backup"],
    )

    return compiled
