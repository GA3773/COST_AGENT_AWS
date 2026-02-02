"""Graph node functions for the EMR cost optimization agent."""

import time
import uuid

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from config import CLUSTER_POLL_INTERVAL, CLUSTER_POLL_MAX_WAIT, get_logger
from services.pricing_service import is_graviton

logger = get_logger(__name__)
audit = get_logger("audit.nodes")


def initialize_node(state: dict) -> dict:
    """Initialize correlation ID and phase tracking."""
    return {
        "correlation_id": str(uuid.uuid4()),
        "current_phase": "initialized",
    }


def call_agent(state: dict, llm_with_tools) -> dict:
    """Invoke the LLM with the current messages and bound tools."""
    messages = state["messages"]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


def route_agent(state: dict) -> str:
    """Route based on the last message: tool_calls -> tools, otherwise -> end or backup.

    Returns the next node name as a string.
    """
    last_msg = state["messages"][-1]

    # If the LLM wants to call tools, route to tools
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools"

    # Check if analysis is complete and recommendation exists
    if state.get("core_analysis") and state.get("human_approved"):
        return "backup"

    return "end"


def extract_analysis_node(state: dict) -> dict:
    """Extract analysis results from tool messages and store in state."""
    # Look through recent messages for analysis data
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage) and msg.name == "analyze_cluster":
            # The analysis results are in the tool output
            # The LLM will have parsed and presented them
            return {"current_phase": "analysis_complete"}
    return {}


def backup_node(state: dict) -> dict:
    """Back up the current Parameter Store configuration before modification."""
    if state.get("error"):
        return {}

    cluster_name = state.get("cluster_name")
    if not cluster_name:
        return {"error": "No cluster_name set in state"}

    try:
        from tools.param_store import get_param_store_config

        result = get_param_store_config.invoke({"cluster_name": cluster_name})
        return {
            "original_config_backup": result["raw_value"],
            "param_store_config": result["config"],
            "current_phase": "backed_up",
            "messages": [AIMessage(content=f"Backed up Parameter Store config for {cluster_name}.")],
        }
    except Exception as e:
        return {
            "error": f"Backup failed: {e}",
            "messages": [AIMessage(content=f"Failed to backup config: {e}")],
        }


def modify_node(state: dict) -> dict:
    """Modify Parameter Store with recommended instance types."""
    if state.get("error"):
        return {}

    cluster_name = state.get("cluster_name")
    core_rec = state.get("core_recommendation", {})
    task_rec = state.get("task_recommendation", {})

    core_type = core_rec.get("recommended_type", "") if core_rec else ""
    task_type = task_rec.get("recommended_type", "") if task_rec else ""

    if not core_type and not task_type:
        return {
            "error": "No recommendations to apply",
            "messages": [AIMessage(content="No recommendations to apply.")],
        }

    # Determine if GravitonAmi flag needs updating
    update_graviton = None
    if core_rec and core_rec.get("arch_change"):
        update_graviton = is_graviton(core_type)
    elif task_rec and task_rec.get("arch_change"):
        update_graviton = is_graviton(task_type)

    try:
        from tools.param_store import modify_param_store

        result = modify_param_store.invoke({
            "cluster_name": cluster_name,
            "core_instance_type": core_type,
            "task_instance_type": task_type,
            "update_graviton_ami": update_graviton,
        })

        changes = []
        if core_type:
            changes.append(f"CORE: {core_rec.get('instance_type', '?')} -> {core_type}")
        if task_type:
            changes.append(f"TASK: {task_rec.get('instance_type', '?')} -> {task_type}")

        return {
            "current_phase": "modified",
            "messages": [AIMessage(content=f"Modified Parameter Store: {'; '.join(changes)}")],
        }
    except Exception as e:
        return {
            "error": f"Modify failed: {e}",
            "messages": [AIMessage(content=f"Failed to modify Parameter Store: {e}")],
        }


def create_node(state: dict) -> dict:
    """Invoke Lambda to create the test cluster."""
    if state.get("error"):
        return {}

    cluster_name = state.get("cluster_name")

    try:
        from tools.lambda_operations import invoke_cluster_lambda

        result = invoke_cluster_lambda.invoke({"cluster_name": cluster_name})

        # Parse cluster ID from result
        new_cluster_id = None
        if "Cluster ID:" in result:
            new_cluster_id = result.split("Cluster ID:")[-1].strip()

        return {
            "new_cluster_id": new_cluster_id,
            "current_phase": "cluster_created",
            "messages": [AIMessage(content=f"Cluster creation triggered. {result}")],
        }
    except Exception as e:
        return {
            "error": f"Create failed: {e}",
            "messages": [AIMessage(content=f"Failed to create cluster: {e}")],
        }


def wait_node(state: dict) -> dict:
    """Poll cluster status until it reaches WAITING/RUNNING or fails."""
    if state.get("error"):
        return {}

    new_cluster_id = state.get("new_cluster_id")
    if not new_cluster_id:
        return {
            "error": "No new cluster ID available",
            "messages": [AIMessage(content="No cluster ID to monitor.")],
        }

    from tools.emr_operations import check_cluster_status

    elapsed = 0
    last_status = ""

    while elapsed < CLUSTER_POLL_MAX_WAIT:
        try:
            result = check_cluster_status.invoke({"cluster_id": new_cluster_id})
        except Exception as e:
            return {
                "error": f"Status check failed: {e}",
                "new_cluster_status": "CHECK_FAILED",
                "messages": [AIMessage(content=f"Failed to check cluster status: {e}")],
            }

        # Parse state from result string
        current_status = "UNKNOWN"
        if ": " in result:
            parts = result.split(": ", 1)
            if len(parts) > 1:
                current_status = parts[1].split(" ")[0].strip()

        if current_status != last_status:
            last_status = current_status
            audit.info(
                "Cluster status change",
                extra={"audit_data": {
                    "event": "cluster_status_change",
                    "cluster_id": new_cluster_id,
                    "new_status": current_status,
                    "correlation_id": state.get("correlation_id"),
                }},
            )

        if current_status in ("WAITING", "RUNNING"):
            return {
                "new_cluster_status": current_status,
                "current_phase": "cluster_ready",
                "messages": [AIMessage(
                    content=f"Cluster {new_cluster_id} is {current_status}."
                )],
            }

        if current_status in ("TERMINATED", "TERMINATED_WITH_ERRORS"):
            return {
                "new_cluster_status": current_status,
                "error": f"Cluster terminated unexpectedly: {current_status}",
                "messages": [AIMessage(
                    content=f"Cluster {new_cluster_id} terminated: {current_status}"
                )],
            }

        time.sleep(CLUSTER_POLL_INTERVAL)
        elapsed += CLUSTER_POLL_INTERVAL

    return {
        "new_cluster_status": "TIMEOUT",
        "error": "Cluster did not reach ready state within timeout",
        "messages": [AIMessage(content=f"Timed out waiting for cluster {new_cluster_id}.")],
    }


def revert_node(state: dict) -> dict:
    """Revert Parameter Store to original config. Always runs."""
    cluster_name = state.get("cluster_name")
    original = state.get("original_config_backup")

    if not cluster_name or not original:
        return {
            "config_reverted": False,
            "current_phase": "revert_skipped",
            "messages": [AIMessage(
                content="Skipped revert: no backup available."
            )],
        }

    try:
        from tools.param_store import revert_param_store

        result = revert_param_store.invoke({
            "cluster_name": cluster_name,
            "original_value": original,
        })
        return {
            "config_reverted": True,
            "current_phase": "reverted",
            "messages": [AIMessage(content=result)],
        }
    except Exception as e:
        return {
            "config_reverted": False,
            "current_phase": "revert_failed",
            "messages": [AIMessage(
                content=f"CRITICAL: Failed to revert Parameter Store: {e}. Manual revert required."
            )],
        }


def report_node(state: dict) -> dict:
    """Generate final optimization report."""
    cluster_name = state.get("cluster_name", "unknown")
    error = state.get("error")
    reverted = state.get("config_reverted", False)
    new_cluster_id = state.get("new_cluster_id", "N/A")
    new_status = state.get("new_cluster_status", "N/A")

    lines = [
        "Optimization Summary",
        "=" * 40,
        f"Cluster: {cluster_name}",
        f"Test cluster: {new_cluster_id} ({new_status})",
        f"Config reverted: {'Yes' if reverted else 'No'}",
    ]

    if error:
        lines.append(f"Error: {error}")
    else:
        lines.append("Status: Completed successfully")

    core_rec = state.get("core_recommendation")
    task_rec = state.get("task_recommendation")
    if core_rec:
        lines.append(f"CORE: {core_rec.get('recommended_type', 'unchanged')}")
    if task_rec:
        lines.append(f"TASK: {task_rec.get('recommended_type', 'unchanged')}")

    report = "\n".join(lines)
    return {
        "final_report": report,
        "current_phase": "complete",
        "messages": [AIMessage(content=report)],
    }
