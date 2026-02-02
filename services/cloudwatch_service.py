"""CloudWatch metrics collection for EMR cluster nodes."""

from datetime import datetime

import numpy as np

from config import get_logger
from services.retry import get_boto3_client, with_backoff

logger = get_logger(__name__)


@with_backoff
def _get_metric(namespace: str, metric_name: str, instance_id: str,
                start: datetime, end: datetime, period: int = 300) -> list[float]:
    """Fetch a single metric's datapoints for one instance."""
    client = get_boto3_client("cloudwatch")
    response = client.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start,
        EndTime=end,
        Period=period,
        Statistics=["Average"],
    )
    datapoints = response.get("Datapoints", [])
    return [dp["Average"] for dp in sorted(datapoints, key=lambda d: d["Timestamp"])]


def get_cpu_metrics(instance_ids: list[str], start: datetime,
                    end: datetime) -> list[float]:
    """Get CPU utilization across instances from AWS/EC2 namespace."""
    all_values = []
    for iid in instance_ids:
        values = _get_metric("AWS/EC2", "CPUUtilization", iid, start, end)
        all_values.extend(values)
    return all_values


def get_memory_metrics(instance_ids: list[str], start: datetime,
                       end: datetime) -> list[float]:
    """Get memory utilization across instances from CWAgent namespace."""
    all_values = []
    for iid in instance_ids:
        values = _get_metric("CWAgent", "mem_used_percent", iid, start, end)
        all_values.extend(values)
    return all_values


def get_cluster_node_metrics(instance_ids: list[str], start: datetime,
                             end: datetime) -> dict:
    """Get combined CPU and memory metrics with statistics.

    Returns:
        dict with cpu_avg, cpu_p95, mem_avg, mem_p95 (all as percentages)
    """
    cpu_values = get_cpu_metrics(instance_ids, start, end)
    mem_values = get_memory_metrics(instance_ids, start, end)

    result = {
        "cpu_avg": 0.0,
        "cpu_p95": 0.0,
        "mem_avg": 0.0,
        "mem_p95": 0.0,
        "cpu_datapoints": len(cpu_values),
        "mem_datapoints": len(mem_values),
    }

    if cpu_values:
        result["cpu_avg"] = round(float(np.mean(cpu_values)), 1)
        result["cpu_p95"] = round(float(np.percentile(cpu_values, 95)), 1)

    if mem_values:
        result["mem_avg"] = round(float(np.mean(mem_values)), 1)
        result["mem_p95"] = round(float(np.percentile(mem_values, 95)), 1)

    logger.info(
        f"Metrics for {len(instance_ids)} instances: "
        f"CPU {result['cpu_avg']}% avg / {result['cpu_p95']}% p95, "
        f"Mem {result['mem_avg']}% avg / {result['mem_p95']}% p95"
    )
    return result
