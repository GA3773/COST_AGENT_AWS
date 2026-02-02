"""CloudWatch metrics collection tool for EMR cluster nodes."""

from datetime import datetime, timezone

from langchain_core.tools import tool

from services import cloudwatch_service, emr_service


@tool
def collect_node_metrics(cluster_id: str, node_type: str) -> dict:
    """Collect CPU and memory metrics for a specific node type in an EMR cluster.

    Args:
        cluster_id: The EMR cluster ID
        node_type: 'CORE' or 'TASK'

    Returns:
        Dict with cpu_avg, cpu_p95, mem_avg, mem_p95, instance_type, instance_count
    """
    # Get instance fleets to find the target fleet
    fleets = emr_service.list_instance_fleets(cluster_id)
    target_fleet = None
    for fleet in fleets:
        if fleet["InstanceFleetType"] == node_type:
            target_fleet = fleet
            break

    if not target_fleet:
        return {"error": f"No {node_type} fleet found for cluster {cluster_id}"}

    # Get instance type from fleet config
    type_configs = target_fleet.get("InstanceTypeSpecifications", [])
    if not type_configs:
        type_configs = target_fleet.get("InstanceTypeConfigs", [])
    instance_type = type_configs[0]["InstanceType"] if type_configs else "unknown"

    # Get EC2 instance IDs for this fleet
    instances = emr_service.list_instances(cluster_id, target_fleet["Id"])
    instance_ids = [
        inst["Ec2InstanceId"]
        for inst in instances
        if inst.get("Ec2InstanceId")
    ]

    if not instance_ids:
        return {
            "error": f"No EC2 instances found for {node_type} fleet",
            "instance_type": instance_type,
            "instance_count": 0,
        }

    # Determine time range from cluster timeline
    cluster = emr_service.describe_cluster(cluster_id)
    timeline = cluster["Status"]["Timeline"]
    start = timeline["CreationDateTime"]
    end = timeline.get("EndDateTime", datetime.now(timezone.utc))

    # Fetch metrics
    metrics = cloudwatch_service.get_cluster_node_metrics(instance_ids, start, end)
    metrics["instance_type"] = instance_type
    metrics["instance_count"] = len(instance_ids)

    return metrics
