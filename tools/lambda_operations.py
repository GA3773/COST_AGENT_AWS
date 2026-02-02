"""Lambda invocation tool for creating EMR clusters."""

import json

from langchain_core.tools import tool

from config import LAMBDA_FUNCTION_NAME, get_logger
from services.retry import get_boto3_client, with_backoff

logger = get_logger(__name__)
audit = get_logger("audit.lambda")


@tool
def invoke_cluster_lambda(cluster_name: str) -> str:
    """Invoke the cluster creation Lambda to create an EMR cluster.

    The Lambda reads the cluster configuration from Parameter Store using the
    cluster_name. The Parameter Store config should already be updated with
    the recommended instance types before calling this.

    Args:
        cluster_name: The cluster name (must match a Parameter Store entry)

    Returns:
        Lambda response with cluster creation status.
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
        }},
    )

    response = _invoke_lambda(payload)
    return response


@with_backoff
def _invoke_lambda(payload: dict) -> str:
    """Invoke the Lambda function with backoff."""
    client = get_boto3_client("lambda")
    response = client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )

    response_payload = json.loads(response["Payload"].read().decode("utf-8"))
    status_code = response.get("StatusCode", 0)

    if status_code == 200:
        body = response_payload.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)
        cluster_id = body.get("cluster_id", "unknown")

        audit.info(
            "Lambda invocation successful",
            extra={"audit_data": {
                "event": "lambda_invoke_success",
                "lambda_function": LAMBDA_FUNCTION_NAME,
                "cluster_id": cluster_id,
                "status_code": status_code,
            }},
        )
        return f"Cluster creation triggered. Cluster ID: {cluster_id}"
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
        return error_msg
