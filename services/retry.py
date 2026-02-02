"""Retry utilities with exponential backoff for AWS API calls."""

import functools
import random
import time

import boto3
from botocore.exceptions import ClientError

from config import (
    AWS_REGION,
    BACKOFF_INITIAL_DELAY,
    BACKOFF_MAX_DELAY,
    BACKOFF_MAX_RETRIES,
    get_logger,
)

logger = get_logger(__name__)

# Cache boto3 clients per service
_clients = {}


def get_boto3_client(service_name: str):
    """Get or create a cached boto3 client for the given service."""
    if service_name not in _clients:
        _clients[service_name] = boto3.client(service_name, region_name=AWS_REGION)
    return _clients[service_name]


def with_backoff(func):
    """Decorator that retries on AWS throttling with exponential backoff and jitter."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        delay = BACKOFF_INITIAL_DELAY
        for attempt in range(BACKOFF_MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code in ("Throttling", "ThrottlingException", "RequestLimitExceeded"):
                    if attempt == BACKOFF_MAX_RETRIES:
                        logger.error(
                            f"Max retries ({BACKOFF_MAX_RETRIES}) exceeded for {func.__name__}"
                        )
                        raise
                    jitter = random.uniform(0, delay * 0.5)
                    sleep_time = min(delay + jitter, BACKOFF_MAX_DELAY)
                    logger.warning(
                        f"Throttled on {func.__name__}, attempt {attempt + 1}/"
                        f"{BACKOFF_MAX_RETRIES}. Retrying in {sleep_time:.1f}s"
                    )
                    time.sleep(sleep_time)
                    delay = min(delay * 2, BACKOFF_MAX_DELAY)
                else:
                    raise

    return wrapper
