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
    runtime_hours = target["runtime_hours"]
    results = {
        "cluster_name": cluster_name,
        "cluster_id": cluster_id,
        "runtime_hours": runtime_hours,
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
            runtime_hours=runtime_hours,
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
            runtime_hours=runtime_hours,
        )
        results["task_analysis"] = task_analysis
    else:
        results["task_analysis"] = {"error": task_metrics["error"]}

    # Format output
    return _format_analysis(results)


def _format_status_label(status: str) -> str:
    """Convert status key to display label."""
    return status.upper().replace("_", " ")


def _format_analysis(results: dict) -> str:
    """Format analysis results with per-dimension breakdown, costs, and alternatives."""
    lines = [
        f"Analysis for {results['cluster_name']} ({results['cluster_id']})",
        f"Runtime: {results['runtime_hours']}h",
        "",
    ]

    for node_key in ["core_analysis", "task_analysis"]:
        analysis = results.get(node_key, {})
        if "error" in analysis:
            lines.append(f"{node_key.replace('_', ' ').upper()}: {analysis['error']}")
            lines.append("")
            continue

        node_type = analysis["node_type"]
        metrics = analysis["metrics"]
        instance_type = analysis["instance_type"]
        count = analysis["instance_count"]
        per_dim = analysis.get("per_dimension", {})
        provisioned = analysis.get("provisioned", {})
        required = analysis.get("required_resources", {})
        over_prov = analysis.get("over_provisioning", {})
        cost_hr = analysis.get("current_cost_per_hour", 0)
        fleet_hr = analysis.get("fleet_cost_per_hour", 0)
        run_cost = analysis.get("run_cost")

        # Header with instance specs and price
        lines.append(f"{node_type} Nodes")
        lines.append(
            f"  Instance Type: {instance_type} "
            f"({provisioned.get('vcpu', '?')} vCPU | "
            f"{provisioned.get('memory_gb', '?')} GB) "
            f"-- ${cost_hr}/hr"
        )
        lines.append(f"  Instance Count: {count}")

        # Fleet cost
        cost_line = f"  Fleet Cost: ${fleet_hr:.2f}/hr"
        if run_cost is not None:
            cost_line += f" | ${run_cost:.2f} for this run"
        lines.append(cost_line)
        lines.append("")

        # Per-dimension utilization with independent status labels
        cpu_status = _format_status_label(per_dim.get("cpu_status", "unknown"))
        mem_status = _format_status_label(per_dim.get("mem_status", "unknown"))
        constraining = per_dim.get("constraining_dimension")

        cpu_suffix = ""
        mem_suffix = ""
        if constraining == "cpu":
            cpu_suffix = " (constraining)"
        elif constraining == "memory":
            mem_suffix = " (constraining)"

        lines.append(
            f"  CPU:    {metrics['cpu_avg']}% avg | {metrics['cpu_p95']}% peak "
            f"-- {cpu_status}{cpu_suffix}"
        )
        lines.append(
            f"  Memory: {metrics['mem_avg']}% avg | {metrics['mem_p95']}% peak "
            f"-- {mem_status}{mem_suffix}"
        )
        lines.append("")

        # Required vs provisioned resources
        req_vcpu = required.get("required_vcpu", 0)
        req_mem = required.get("required_memory_gb", 0)
        lines.append(
            f"  Required: ~{req_vcpu} vCPU | ~{req_mem} GB memory (incl. 20% headroom)"
        )

        over_parts = []
        cpu_ratio = over_prov.get("cpu_ratio", 1)
        mem_ratio = over_prov.get("mem_ratio", 1)
        if cpu_ratio > 1.5:
            over_parts.append(f"CPU {cpu_ratio}x over-provisioned")
        if mem_ratio > 1.5:
            over_parts.append(f"Memory {mem_ratio}x over-provisioned")
        prov_line = (
            f"  Provisioned: {provisioned.get('vcpu', '?')} vCPU | "
            f"{provisioned.get('memory_gb', '?')} GB"
        )
        if over_parts:
            prov_line += f" -- {', '.join(over_parts)}"
        lines.append(prov_line)
        lines.append("")

        # Profile and overall status
        profile = analysis.get("workload_profile", "unknown").replace("_", " ").title()
        overall_status = _format_status_label(analysis["sizing_status"])
        status_note = overall_status
        if per_dim.get("asymmetric") and constraining:
            status_note += f" ({constraining}-constrained, other dimension oversized)"
        lines.append(f"  Profile: {profile}")
        lines.append(f"  Status: {status_note}")
        lines.append("")

        # Recommendation
        rec = analysis.get("recommendation")
        if rec:
            if rec.get("recommendation_kind") == "none_cheaper":
                lines.append("  Recommendation: No cheaper alternative found")
                if rec.get("explanation"):
                    for exp_line in rec["explanation"].split("\n"):
                        lines.append(f"  {exp_line}")
            else:
                lines.append(
                    f"  Recommendation: {instance_type} -> "
                    f"{rec['recommended_type']} ({rec['recommendation_kind']})"
                )
                lines.append(f"  Savings: {rec['savings_percent']}% per instance-hour")
                if rec.get("arch_change"):
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

        # Alternatives table
        alternatives = analysis.get("alternatives", [])
        near_misses = analysis.get("near_misses")
        if alternatives:
            lines.append("  Alternatives:")
            for alt in alternatives:
                lines.append(
                    f"    {alt['instance_type']} ({alt['vcpu']} vCPU, "
                    f"{alt['memory_gb']} GB, ${alt['price_per_hour']}/hr) "
                    f"-- {alt['savings_pct']}% savings"
                )
            lines.append("")
        elif near_misses:
            lines.append("  Alternatives: None cheaper found.")
            for nm in near_misses:
                shortfalls = []
                if nm.get("shortfall_mem", 0) > 0:
                    shortfalls.append(
                        f"insufficient memory (need {required.get('required_memory_gb', '?')} GB)"
                    )
                if nm.get("shortfall_vcpu", 0) > 0:
                    shortfalls.append(
                        f"insufficient vCPU (need {required.get('required_vcpu', '?')})"
                    )
                if shortfalls:
                    lines.append(
                        f"    {nm['instance_type']} ({nm['vcpu']} vCPU, "
                        f"{nm['memory_gb']} GB, ${nm['price_per_hour']}/hr) "
                        f"-- {'; '.join(shortfalls)}"
                    )
            lines.append("")

    return "\n".join(lines)
