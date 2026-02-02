"""EMR cluster listing and status tools."""

from langchain_core.tools import tool

from services import emr_service


@tool
def list_transient_clusters() -> str:
    """List recent transient EMR clusters (runtime < 6 hours, terminated/completed).

    Returns a formatted list of clusters with their names, runtimes, and states.
    """
    clusters = emr_service.get_transient_clusters()

    if not clusters:
        return "No transient clusters found in the last 24 hours."

    lines = [f"Found {len(clusters)} transient cluster(s):\n"]
    for c in clusters:
        lines.append(
            f"  {c['name']:<30s} {c['runtime_hours']:>5.1f}h   "
            f"{c['state']:<15s} ({c['cluster_id']})"
        )
    return "\n".join(lines)


@tool
def check_cluster_status(cluster_id: str) -> str:
    """Check the current status of an EMR cluster.

    Args:
        cluster_id: The EMR cluster ID (e.g., j-3KF82HD)

    Returns:
        Cluster state and status details.
    """
    cluster = emr_service.describe_cluster(cluster_id)
    status = cluster["Status"]
    state = status["State"]
    state_change = status.get("StateChangeReason", {})
    reason_code = state_change.get("Code", "")
    reason_msg = state_change.get("Message", "")

    result = f"Cluster {cluster_id}: {state}"
    if reason_code:
        result += f" ({reason_code}: {reason_msg})"
    return result
