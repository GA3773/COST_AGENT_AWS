"""Tool for checking background optimization status."""

from langchain_core.tools import tool

from services.background_monitor import monitor


@tool
def check_optimization_status() -> str:
    """Check the status of the current or most recent optimization.

    Returns status information including:
    - Whether an optimization is active
    - Cluster name and request ID
    - Current monitoring status (pending/monitoring/reverted/failed)
    - Whether the param store has been reverted

    Use this when the user asks about optimization progress or status.
    """
    status = monitor.get_status()

    if not status.get("active") and status.get("status") is None:
        return "No optimization in progress or recently completed."

    return status.get("message", f"Status: {status}")
