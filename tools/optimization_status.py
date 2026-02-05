"""Tool for checking background optimization status."""

from langchain_core.tools import tool

from services.background_monitor import monitor


@tool
def check_optimization_status() -> str:
    """Check the real-time status of background cluster monitoring.

    Returns live status from the background monitor including:
    - Whether monitoring is active
    - Current cluster state (STARTING, RUNNING, etc.)
    - Elapsed time
    - Whether Parameter Store has been reverted

    Note: For cluster name and job ID, check your session context
    (this info is already available to you from previous conversation).
    """
    status = monitor.get_status()

    if not status.get("active") and status.get("status") is None:
        return (
            "No active background monitoring. "
            "Check your session context for cluster name and job ID from the optimization you started."
        )

    lines = ["**Background Monitor Status**"]

    if status.get("cluster_name"):
        lines.append(f"- Cluster: {status['cluster_name']}")
    if status.get("cluster_state"):
        lines.append(f"- State: {status['cluster_state']}")
    if status.get("elapsed_seconds") is not None:
        elapsed = status["elapsed_seconds"]
        mins, secs = divmod(int(elapsed), 60)
        lines.append(f"- Elapsed: {mins}m {secs}s")
    if status.get("status"):
        lines.append(f"- Monitor status: {status['status']}")
    if status.get("reverted"):
        lines.append("- Parameter Store: **reverted to original**")
    if status.get("message"):
        lines.append(f"- {status['message']}")

    return "\n".join(lines)
