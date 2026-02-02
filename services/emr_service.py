"""EMR API wrapper with backoff and pagination."""

from datetime import datetime, timedelta, timezone

from config import CLUSTER_LOOKBACK_HOURS, TRANSIENT_RUNTIME_HOURS, get_logger
from services.retry import get_boto3_client, with_backoff

logger = get_logger(__name__)


@with_backoff
def list_clusters(created_after: datetime, states: list[str]) -> list[dict]:
    """List EMR clusters created after a given time, with pagination."""
    client = get_boto3_client("emr")
    clusters = []
    params = {"CreatedAfter": created_after, "ClusterStates": states}

    while True:
        response = client.list_clusters(**params)
        clusters.extend(response.get("Clusters", []))
        marker = response.get("Marker")
        if not marker:
            break
        params["Marker"] = marker

    return clusters


@with_backoff
def describe_cluster(cluster_id: str) -> dict:
    """Get detailed information about a single cluster."""
    client = get_boto3_client("emr")
    response = client.describe_cluster(ClusterId=cluster_id)
    return response["Cluster"]


@with_backoff
def list_instance_fleets(cluster_id: str) -> list[dict]:
    """Get instance fleet configurations for a cluster."""
    client = get_boto3_client("emr")
    response = client.list_instance_fleets(ClusterId=cluster_id)
    return response.get("InstanceFleets", [])


@with_backoff
def list_instances(cluster_id: str, instance_fleet_id: str = None) -> list[dict]:
    """Get EC2 instances for a cluster, optionally filtered by fleet."""
    client = get_boto3_client("emr")
    params = {"ClusterId": cluster_id}
    if instance_fleet_id:
        params["InstanceFleetId"] = instance_fleet_id

    instances = []
    while True:
        response = client.list_instances(**params)
        instances.extend(response.get("Instances", []))
        marker = response.get("Marker")
        if not marker:
            break
        params["Marker"] = marker

    return instances


def get_transient_clusters() -> list[dict]:
    """List transient clusters from the last 24 hours.

    Returns clusters that are TERMINATED or COMPLETED with runtime < 6 hours.
    """
    created_after = datetime.now(timezone.utc) - timedelta(hours=CLUSTER_LOOKBACK_HOURS)
    states = ["TERMINATED", "COMPLETED", "TERMINATED_WITH_ERRORS"]

    raw_clusters = list_clusters(created_after, states)
    transient = []

    for cluster in raw_clusters:
        start = cluster.get("Status", {}).get("Timeline", {}).get("CreationDateTime")
        end = cluster.get("Status", {}).get("Timeline", {}).get("EndDateTime")
        if not start or not end:
            continue

        runtime_hours = (end - start).total_seconds() / 3600
        if runtime_hours < TRANSIENT_RUNTIME_HOURS:
            transient.append({
                "cluster_id": cluster["Id"],
                "name": cluster["Name"],
                "state": cluster["Status"]["State"],
                "runtime_hours": round(runtime_hours, 1),
                "created": start.isoformat(),
                "ended": end.isoformat(),
            })

    logger.info(
        f"Found {len(transient)} transient clusters out of {len(raw_clusters)} total"
    )
    return transient
