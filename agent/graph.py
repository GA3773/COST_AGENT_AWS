"""LangGraph workflow definition for the EMR cost optimization agent."""

import functools

from langchain_core.messages import SystemMessage
from langchain_openai import AzureChatOpenAI
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
    wait_node,
)
from agent.prompts import SYSTEM_PROMPT
from agent.state import AgentState
from config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
)
from tools import ALL_TOOLS


def build_graph():
    """Build and compile the LangGraph agent workflow.

    Returns:
        Compiled graph with MemorySaver checkpointer for interrupt/resume.
    """
    # Create LLM
    llm = AzureChatOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_deployment=AZURE_OPENAI_DEPLOYMENT,
        temperature=0,
    )

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
    graph.add_node("wait", wait_node)
    graph.add_node("revert", revert_node)
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

    # Execution pipeline: backup -> modify -> create -> wait -> revert -> report -> END
    graph.add_edge("backup", "modify")

    # Modify can error -> jump to revert
    def modify_route(state: dict) -> str:
        return "revert" if state.get("error") else "create"
    graph.add_conditional_edges("modify", modify_route, {
        "create": "create",
        "revert": "revert",
    })

    # Create can error -> jump to revert
    def create_route(state: dict) -> str:
        return "revert" if state.get("error") else "wait"
    graph.add_conditional_edges("create", create_route, {
        "wait": "wait",
        "revert": "revert",
    })

    # Wait can error -> jump to revert
    def wait_route(state: dict) -> str:
        return "revert"  # Always revert after wait, success or failure
    graph.add_conditional_edges("wait", wait_route, {
        "revert": "revert",
    })

    graph.add_edge("revert", "report")
    graph.add_edge("report", END)

    # Compile with checkpointer and interrupt before backup (approval gate)
    checkpointer = MemorySaver()
    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["backup"],
    )

    return compiled
