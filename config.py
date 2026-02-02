"""Configuration and constants for COST_AGENT_AWS."""

import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# --- Azure OpenAI ---
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

# --- Azure Service Principal (hybrid auth with PEM certificate) ---
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
AZURE_SPN_CLIENT_ID = os.getenv("AZURE_SPN_CLIENT_ID", "")
AZURE_PEM_PATH = os.getenv("AZURE_PEM_PATH", "azure_cert.pem")
AZURE_USER_ID = os.getenv("AZURE_USER_ID", "cost-agent")

# --- AWS ---
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
PARAM_STORE_PREFIX = os.getenv(
    "PARAM_STORE_PREFIX", "/application/ecdp-config/prod/EMR-BASE/"
)
LAMBDA_FUNCTION_NAME = os.getenv(
    "LAMBDA_FUNCTION_NAME", "app-job-submit-lambda-prod"
)

# --- Safety Guardrails ---
MAX_CLUSTER_INSTANCES = int(os.getenv("MAX_CLUSTER_INSTANCES", "20"))
AUTO_TERMINATE_MINUTES = int(os.getenv("AUTO_TERMINATE_MINUTES", "120"))
MAX_TEST_COST_DOLLARS = float(os.getenv("MAX_TEST_COST_DOLLARS", "50"))

# --- Sizing Thresholds ---
# Uses the HIGHER of CPU and Memory utilization
SIZING_THRESHOLDS = {
    "heavily_oversized": {"avg_max": 25, "peak_max": 35},
    "moderately_oversized": {"avg_max": 50, "peak_max": 60},
    "right_sized": {"avg_max": 70, "peak_max": 80},
    # Anything above right_sized thresholds is undersized
}

# --- Backoff Constants ---
BACKOFF_INITIAL_DELAY = 1.0  # seconds
BACKOFF_MAX_DELAY = 30.0  # seconds
BACKOFF_MAX_RETRIES = 5

# --- Analysis ---
HEADROOM_FACTOR = 1.2  # 20% headroom on peak utilization
TRANSIENT_RUNTIME_HOURS = 6  # max runtime for transient clusters
CLUSTER_LOOKBACK_HOURS = 24  # how far back to search for clusters

# --- Graviton Detection ---
GRAVITON_FAMILIES = {
    "m6g", "m6gd", "m7g", "m7gd",
    "r6g", "r6gd", "r7g", "r7gd",
    "c6g", "c6gd", "c7g", "c7gd",
    "a1",
}

# --- Cluster Monitoring ---
CLUSTER_POLL_INTERVAL = 30  # seconds between status checks
CLUSTER_POLL_MAX_WAIT = 1800  # 30 minutes max wait


# --- Audit Logging ---
class AuditFormatter(logging.Formatter):
    """JSON structured log formatter for audit events."""

    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "audit_data"):
            log_data.update(record.audit_data)
        return json.dumps(log_data)


def get_logger(name: str) -> logging.Logger:
    """Create a logger with JSON audit formatting."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(AuditFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
