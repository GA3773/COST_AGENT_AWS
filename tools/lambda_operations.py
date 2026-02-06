"""Lambda invocation tool for creating EMR clusters."""

import json
import uuid

from langchain_core.tools import tool

from config import LAMBDA_FUNCTION_NAME, get_logger
from services.retry import get_boto3_client, with_backoff

logger = get_logger(__name__)
audit = get_logger("audit.lambda")


@tool
def invoke_cluster_lambda(cluster_name: str, original_config: str = "") -> str:
    """Invoke the cluster creation Lambda to create an EMR cluster.

    The Lambda reads the cluster configuration from Parameter Store using the
    cluster_name. The Parameter Store config should already be updated with
    the recommended instance types before calling this.

    IMPORTANT: If you modified the Parameter Store config, you MUST provide
    the original_config parameter so the system can automatically revert
    after the cluster starts. Get this value from get_param_store_config's
    'raw_value' field BEFORE modifying.

    Args:
        cluster_name: The cluster name (must match a Parameter Store entry)
        original_config: The original Parameter Store value (from raw_value).
                        If provided, background monitoring will auto-revert
                        the config after cluster reaches STARTING state.

    Returns:
        Lambda response with cluster creation status and monitoring info.
    """
    payload = {
        "resource": "/executions/clusters",
        "path": "/executions/clusters",
        "body": json.dumps({
            "cluster_name": cluster_name,
            "job_type": "CLUSTER",
            "request_type": "CREATE",
            "fifo_key": cluster_name,
        }),
        "httpMethod": "POST",
    }

    audit.info(
        "Lambda invocation",
        extra={"audit_data": {
            "event": "lambda_invoke",
            "cluster_name": cluster_name,
            "lambda_function": LAMBDA_FUNCTION_NAME,
            "payload": payload,
            "has_original_config": bool(original_config),
        }},
    )

    response, request_id = _invoke_lambda(payload)

    # If original_config provided, start background monitoring for auto-revert
    monitor_message = ""
    if original_config and "successful" in response.lower():
        try:
            from services.background_monitor import monitor

            task_id = str(uuid.uuid4())
            monitor.start_monitoring(
                task_id=task_id,
                cluster_name=cluster_name,
                request_id=request_id,
                original_config=original_config,
            )
            monitor_message = (
                "\n\n**Background Monitoring Started**\n"
                "The Parameter Store config will automatically revert to original "
                "once the cluster reaches STARTING state (typically 10-12 minutes)."
            )
            logger.info(f"[LAMBDA] Background monitor started, task_id={task_id}")
        except Exception as e:
            logger.error(f"[LAMBDA] Failed to start background monitor: {e}")
            monitor_message = (
                f"\n\n**Warning:** Could not start background monitoring: {e}\n"
                "You may need to manually revert the Parameter Store config."
            )
    elif not original_config:
        monitor_message = (
            "\n\n**Note:** No original_config provided. Background monitoring not started. "
            "If you modified Parameter Store, remember to revert it manually using revert_param_store."
        )

    return response + monitor_message


@with_backoff
def _invoke_lambda(payload: dict) -> tuple[str, str]:
    """Invoke the Lambda function with backoff. Returns (response_text, request_id)."""
    client = get_boto3_client("lambda")
    response = client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )

    response_payload = json.loads(response["Payload"].read().decode("utf-8"))
    status_code = response.get("StatusCode", 0)

    logger.info(f"[LAMBDA] Response status_code={status_code}, payload={response_payload}")

    if status_code == 200:
        body = response_payload.get("body", "{}")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                body = {"raw": body}

        # Lambda returns request_id (Step Functions execution), not cluster_id
        request_id = body.get("request_id", body.get("cluster_id", "unknown"))

        audit.info(
            "Lambda invocation successful",
            extra={"audit_data": {
                "event": "lambda_invoke_success",
                "lambda_function": LAMBDA_FUNCTION_NAME,
                "request_id": request_id,
                "status_code": status_code,
                "body": body,
            }},
        )

        # Return detailed output for user visibility
        output_lines = [
            "Lambda invocation successful.",
            f"Request ID: {request_id}",
            "",
            "Full response:",
            json.dumps(body, indent=2),
        ]
        return "\n".join(output_lines), request_id
    else:
        error_msg = f"Lambda returned status {status_code}: {response_payload}"
        audit.error(
            "Lambda invocation failed",
            extra={"audit_data": {
                "event": "lambda_invoke_failure",
                "lambda_function": LAMBDA_FUNCTION_NAME,
                "status_code": status_code,
                "error": str(response_payload),
            }},
        )
        return error_msg, "unknown"
