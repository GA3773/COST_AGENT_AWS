"""CloudWatch metrics collection tool for EMR cluster nodes."""

from datetime import datetime, timezone

from langchain_core.tools import tool

from services import cloudwatch_service, emr_service


@tool
def collect_node_metrics(cluster_id: str, node_type: str) -> dict:
    """Collect CPU and memory metrics for a specific node type in an EMR cluster.

    Supports both Instance Fleets and Instance Groups based clusters.

    Args:
        cluster_id: The EMR cluster ID
        node_type: 'CORE' or 'TASK'

    Returns:
        Dict with cpu_avg, cpu_p95, mem_avg, mem_p95, instance_type, instance_count, config_type
    """
    # Detect whether cluster uses fleets or groups
    config = emr_service.get_cluster_instance_config(cluster_id)
    config_type = config["type"]
    configs = config["configs"]

    if config_type == "fleets":
        return _collect_metrics_from_fleet(cluster_id, node_type, configs)
    elif config_type == "groups":
        return _collect_metrics_from_group(cluster_id, node_type, configs)
    else:
        return {"error": f"Could not determine instance configuration type for cluster {cluster_id}"}


def _collect_metrics_from_fleet(cluster_id: str, node_type: str, fleets: list) -> dict:
    """Collect metrics from an Instance Fleet based cluster."""
    # Find the target fleet
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
    instances = emr_service.list_instances(cluster_id, instance_fleet_id=target_fleet["Id"])
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
            "config_type": "fleets",
        }

    # Fetch metrics
    metrics = _fetch_cluster_metrics(cluster_id, instance_ids)
    metrics["instance_type"] = instance_type
    metrics["instance_count"] = len(instance_ids)
    metrics["config_type"] = "fleets"

    return metrics


def _collect_metrics_from_group(cluster_id: str, node_type: str, groups: list) -> dict:
    """Collect metrics from an Instance Group based cluster."""
    # Find the target group
    target_group = None
    for group in groups:
        if group["InstanceGroupType"] == node_type:
            target_group = group
            break

    if not target_group:
        return {"error": f"No {node_type} group found for cluster {cluster_id}"}

    # Get instance type from group config
    instance_type = target_group.get("InstanceType", "unknown")

    # Get EC2 instance IDs for this group
    instances = emr_service.list_instances(cluster_id, instance_group_id=target_group["Id"])
    instance_ids = [
        inst["Ec2InstanceId"]
        for inst in instances
        if inst.get("Ec2InstanceId")
    ]

    if not instance_ids:
        return {
            "error": f"No EC2 instances found for {node_type} group",
            "instance_type": instance_type,
            "instance_count": 0,
            "config_type": "groups",
        }

    # Fetch metrics
    metrics = _fetch_cluster_metrics(cluster_id, instance_ids)
    metrics["instance_type"] = instance_type
    metrics["instance_count"] = len(instance_ids)
    metrics["config_type"] = "groups"

    return metrics


def _fetch_cluster_metrics(cluster_id: str, instance_ids: list) -> dict:
    """Fetch CloudWatch metrics for a list of EC2 instances."""
    # Determine time range from cluster timeline
    cluster = emr_service.describe_cluster(cluster_id)
    timeline = cluster["Status"]["Timeline"]
    start = timeline["CreationDateTime"]
    end = timeline.get("EndDateTime", datetime.now(timezone.utc))

    # Fetch metrics
    return cloudwatch_service.get_cluster_node_metrics(instance_ids, start, end)
