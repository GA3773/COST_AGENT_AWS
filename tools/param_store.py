"""Parameter Store read/modify/revert tools."""

import json
import logging

from langchain_core.tools import tool

from config import PARAM_STORE_PREFIX, get_logger
from services.pricing_service import is_graviton
from services.retry import get_boto3_client, with_backoff

logger = get_logger(__name__)
audit = get_logger("audit.param_store")


@with_backoff
def _get_parameter(path: str) -> str:
    """Read raw value from Parameter Store."""
    logger.debug(f"[PARAM_STORE] Reading parameter: {path}")
    client = get_boto3_client("ssm")
    response = client.get_parameter(Name=path, WithDecryption=True)
    logger.debug(f"[PARAM_STORE] Successfully read parameter: {path}")
    return response["Parameter"]["Value"]


@with_backoff
def _put_parameter(path: str, value: str) -> None:
    """Write value to Parameter Store."""
    logger.info(f"[PARAM_STORE] Writing parameter: {path}")
    client = get_boto3_client("ssm")
    client.put_parameter(Name=path, Value=value, Type="String", Overwrite=True)
    logger.info(f"[PARAM_STORE] Successfully wrote parameter: {path}")


@tool
def get_param_store_config(cluster_name: str) -> dict:
    """Read the EMR cluster configuration from AWS Parameter Store.

    Args:
        cluster_name: The cluster name (maps to Parameter Store key)

    Returns:
        Dict with 'raw_value' (original string) and 'config' (parsed dict)
    """
    path = f"{PARAM_STORE_PREFIX}{cluster_name}"
    logger.info(f"[PARAM_STORE] GET config for cluster={cluster_name}, path={path}")

    raw_value = _get_parameter(path)

    audit.info(
        "Parameter Store read",
        extra={"audit_data": {
            "event": "param_store_read",
            "cluster_name": cluster_name,
            "param_path": path,
        }},
    )

    config = json.loads(raw_value)
    logger.info(f"[PARAM_STORE] GET success: cluster={cluster_name}, path={path}")
    return {"raw_value": raw_value, "config": config, "param_path": path}


@tool
def modify_param_store(cluster_name: str, core_instance_type: str = "",
                       task_instance_type: str = "",
                       update_graviton_ami: bool | None = None) -> str:
    """Modify instance types in Parameter Store config for CORE and/or TASK fleets.

    Only changes InstanceType values within InstanceTypeConfigs for CORE and TASK
    fleets. Everything else (EBS, capacity, subnets, etc.) remains unchanged.

    Args:
        cluster_name: The cluster name
        core_instance_type: New instance type for CORE fleet (empty string to skip)
        task_instance_type: New instance type for TASK fleet (empty string to skip)
        update_graviton_ami: If set, update the GravitonAmi flag

    Returns:
        Summary of changes made.
    """
    path = f"{PARAM_STORE_PREFIX}{cluster_name}"
    logger.info(f"[PARAM_STORE] MODIFY starting: cluster={cluster_name}, path={path}")
    logger.info(f"[PARAM_STORE] MODIFY params: core_type={core_instance_type or 'unchanged'}, "
                f"task_type={task_instance_type or 'unchanged'}, graviton_ami={update_graviton_ami}")

    raw_value = _get_parameter(path)
    config = json.loads(raw_value)

    # The Instances field is a JSON string within the config
    instances_str = config.get("Instances", "{}")
    if isinstance(instances_str, str):
        instances = json.loads(instances_str)
    else:
        instances = instances_str

    changes = []
    fleets = instances.get("InstanceFleets", [])

    for fleet in fleets:
        fleet_type = fleet.get("InstanceFleetType", "")
        new_type = None

        if fleet_type == "CORE" and core_instance_type:
            new_type = core_instance_type
        elif fleet_type == "TASK" and task_instance_type:
            new_type = task_instance_type

        if new_type:
            type_configs = fleet.get("InstanceTypeConfigs", [])
            if type_configs:
                # Only modify the FIRST (primary) instance type config
                # Secondary/fallback options are left unchanged to avoid duplicates
                old_type = type_configs[0].get("InstanceType", "")
                type_configs[0]["InstanceType"] = new_type
                changes.append(f"{fleet_type}: {old_type} -> {new_type}")
                if len(type_configs) > 1:
                    logger.info(f"[PARAM_STORE] Fleet {fleet_type} has {len(type_configs)} instance configs, "
                                f"only modified primary (index 0), secondary options unchanged")

    # Update GravitonAmi flag if needed
    if update_graviton_ami is not None:
        old_flag = config.get("GravitonAmi")
        config["GravitonAmi"] = update_graviton_ami
        changes.append(f"GravitonAmi: {old_flag} -> {update_graviton_ami}")

    # Write back: re-serialize Instances as JSON string within config
    config["Instances"] = json.dumps(instances)
    new_raw = json.dumps(config)

    logger.info(f"[PARAM_STORE] MODIFY writing changes to path={path}: {changes}")
    _put_parameter(path, new_raw)

    audit.info(
        "Parameter Store modified",
        extra={"audit_data": {
            "event": "param_store_modify",
            "cluster_name": cluster_name,
            "param_path": path,
            "changes": changes,
        }},
    )

    logger.info(f"[PARAM_STORE] MODIFY complete: cluster={cluster_name}, path={path}, changes={changes}")
    return f"Modified Parameter Store for {cluster_name}: " + "; ".join(changes)


@tool
def revert_param_store(cluster_name: str, original_value: str) -> str:
    """Revert Parameter Store to its original configuration.

    This MUST be called after cluster creation, regardless of success or failure.

    Args:
        cluster_name: The cluster name
        original_value: The exact original raw value to restore

    Returns:
        Confirmation of revert.
    """
    path = f"{PARAM_STORE_PREFIX}{cluster_name}"
    logger.info(f"[PARAM_STORE] REVERT starting: cluster={cluster_name}, path={path}")

    try:
        _put_parameter(path, original_value)
        audit.info(
            "Parameter Store reverted",
            extra={"audit_data": {
                "event": "param_store_revert",
                "cluster_name": cluster_name,
                "param_path": path,
                "success": True,
            }},
        )
        logger.info(f"[PARAM_STORE] REVERT success: cluster={cluster_name}, path={path}")
        return f"Successfully reverted Parameter Store for {cluster_name} to original config."
    except Exception as e:
        audit.error(
            "Parameter Store revert FAILED",
            extra={"audit_data": {
                "event": "param_store_revert",
                "cluster_name": cluster_name,
                "param_path": path,
                "success": False,
                "error": str(e),
            }},
        )
        logger.error(f"[PARAM_STORE] REVERT FAILED: cluster={cluster_name}, path={path}, error={e}")
        return f"FAILED to revert Parameter Store for {cluster_name}: {e}"
