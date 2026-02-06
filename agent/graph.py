"""LangGraph workflow definition for the EMR cost optimization agent."""

from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from agent.nodes import call_agent, initialize_node
from agent.prompts import SYSTEM_PROMPT
from agent.state import AgentState
from services.azure_openai import create_llm
from tools import ALL_TOOLS


def build_graph():
    """Build and compile the LangGraph agent workflow.

    The graph is now simplified to let the LLM decide the workflow:
    - LLM can call tools in any order based on user intent
    - For "full optimization": read config → modify → invoke Lambda
    - For "modify only": read config → modify → done
    - For "just analyze": analyze → present recommendations

    Returns:
        Compiled graph with MemorySaver checkpointer.
    """
    # Create LLM with hybrid auth (Service Principal + API key)
    llm = create_llm()

    # Bind tools to LLM
    llm_with_tools = llm.bind_tools(ALL_TOOLS)

    # Create the agent node (closure over llm_with_tools)
    def agent_node(state: dict) -> dict:
        # Build system prompt with current optimization context
        system_content = SYSTEM_PROMPT

        # Inject current state context so LLM knows about ongoing optimization
        context_parts = []
        if state.get("cluster_name"):
            context_parts.append(f"Current cluster: {state['cluster_name']}")
        if state.get("cluster_id"):
            context_parts.append(f"Cluster ID: {state['cluster_id']}")
        if state.get("original_config_backup"):
            context_parts.append("Original config: **backed up** (available for revert)")
        if state.get("new_cluster_id"):
            context_parts.append(f"New (optimized) cluster ID: {state['new_cluster_id']}")
        if state.get("optimization_status"):
            context_parts.append(f"Optimization status: {state['optimization_status']}")
        if state.get("optimization_request_id"):
            context_parts.append(f"Job ID: {state['optimization_request_id']}")
        if state.get("core_recommendation"):
            rec = state["core_recommendation"]
            context_parts.append(f"CORE recommendation: {rec.get('instance_type', 'N/A')}")
        if state.get("task_recommendation"):
            rec = state["task_recommendation"]
            context_parts.append(f"TASK recommendation: {rec.get('instance_type', 'N/A')}")

        if context_parts:
            context_block = "\n\n## Current Session Context\n" + "\n".join(f"- {p}" for p in context_parts)
            system_content = SYSTEM_PROMPT + context_block

        # Inject system prompt if not already present or update it
        messages = list(state["messages"])
        if messages and isinstance(messages[0], SystemMessage):
            messages[0] = SystemMessage(content=system_content)
        else:
            messages = [SystemMessage(content=system_content)] + messages
        state = {**state, "messages": messages}

        return call_agent(state, llm_with_tools)

    def route_agent(state: dict) -> str:
        """Route based on the last message: tool_calls -> tools, otherwise -> end."""
        last_msg = state["messages"][-1]

        # If the LLM wants to call tools, route to tools
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return "tools"

        return "end"

    # Build graph - simplified to agent ↔ tools loop
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("initialize", initialize_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(ALL_TOOLS))

    # Set entry point
    graph.set_entry_point("initialize")

    # Edges
    graph.add_edge("initialize", "agent")

    # Agent routing: tools or end
    graph.add_conditional_edges("agent", route_agent, {
        "tools": "tools",
        "end": END,
    })

    # Tools loop back to agent
    graph.add_edge("tools", "agent")

    # Compile with checkpointer (no interrupt - LLM asks for confirmation via conversation)
    checkpointer = MemorySaver()
    compiled = graph.compile(checkpointer=checkpointer)

    return compiled
