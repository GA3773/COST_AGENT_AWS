"""Composite cluster analysis tool."""

import json

from langchain_core.tools import tool

from services import analyzer_service, emr_service
from tools.metrics import collect_node_metrics


@tool
def analyze_cluster(cluster_name: str) -> str:
    """Analyze an EMR cluster's utilization and generate optimization recommendations.

    Analyzes CORE and TASK nodes separately. Provides sizing status and
    instance type recommendations with estimated cost savings.

    Args:
        cluster_name: The cluster name to analyze (must match a recent transient cluster)

    Returns:
        Formatted analysis results with recommendations.
    """
    # Find the cluster by name
    clusters = emr_service.get_transient_clusters()
    target = None
    for c in clusters:
        if c["name"] == cluster_name:
            target = c
            break

    if not target:
        return f"Cluster '{cluster_name}' not found among recent transient clusters."

    cluster_id = target["cluster_id"]
    results = {
        "cluster_name": cluster_name,
        "cluster_id": cluster_id,
        "runtime_hours": target["runtime_hours"],
    }

    # Analyze CORE nodes
    core_metrics = collect_node_metrics.invoke(
        {"cluster_id": cluster_id, "node_type": "CORE"}
    )
    if "error" not in core_metrics:
        core_analysis = analyzer_service.analyze_node_type(
            "CORE",
            core_metrics["instance_type"],
            core_metrics["instance_count"],
            core_metrics,
        )
        results["core_analysis"] = core_analysis
    else:
        results["core_analysis"] = {"error": core_metrics["error"]}

    # Analyze TASK nodes
    task_metrics = collect_node_metrics.invoke(
        {"cluster_id": cluster_id, "node_type": "TASK"}
    )
    if "error" not in task_metrics:
        task_analysis = analyzer_service.analyze_node_type(
            "TASK",
            task_metrics["instance_type"],
            task_metrics["instance_count"],
            task_metrics,
        )
        results["task_analysis"] = task_analysis
    else:
        results["task_analysis"] = {"error": task_metrics["error"]}

    # Format output
    return _format_analysis(results)


def _format_analysis(results: dict) -> str:
    """Format analysis results as a readable string."""
    lines = [
        f"Analysis for {results['cluster_name']} ({results['cluster_id']})",
        f"Runtime: {results['runtime_hours']}h",
        "",
    ]

    for node_key in ["core_analysis", "task_analysis"]:
        analysis = results.get(node_key, {})
        if "error" in analysis:
            lines.append(f"{node_key.upper()}: {analysis['error']}")
            lines.append("")
            continue

        node_type = analysis["node_type"]
        metrics = analysis["metrics"]
        lines.append(
            f"{node_type} NODES ({analysis['instance_count']}x {analysis['instance_type']})"
        )
        lines.append(
            f"  CPU:    {metrics['cpu_avg']}% avg | {metrics['cpu_p95']}% peak"
        )
        lines.append(
            f"  Memory: {metrics['mem_avg']}% avg | {metrics['mem_p95']}% peak"
        )
        lines.append(
            f"  Status: {analysis['sizing_status'].upper().replace('_', ' ')}"
        )
        lines.append(f"  Profile: {analysis['workload_profile']}")

        rec = analysis.get("recommendation")
        if rec:
            lines.append(
                f"  Recommendation: {analysis['instance_type']} -> "
                f"{rec['recommended_type']} ({rec['recommendation_kind']})"
            )
            lines.append(f"  Savings: {rec['savings_percent']}% per instance-hour")
            if rec["arch_change"]:
                lines.append(
                    f"  Architecture change: {rec['current_arch']} -> "
                    f"{rec['recommended_arch']}"
                )
        else:
            if analysis["sizing_status"] == "right_sized":
                lines.append("  Recommendation: No change needed (right-sized)")
            elif analysis["sizing_status"] == "undersized":
                lines.append("  Recommendation: Consider upsizing (out of scope)")
            else:
                lines.append("  Recommendation: No cheaper alternative found")
        lines.append("")

    return "\n".join(lines)
