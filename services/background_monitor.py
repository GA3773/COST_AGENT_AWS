"""Background cluster monitoring service for async param store revert."""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable

from config import (
    BACKGROUND_POLL_INTERVAL,
    BACKGROUND_POLL_TIMEOUT,
    get_logger,
)

logger = get_logger(__name__)
audit = get_logger("audit.background_monitor")


class MonitorStatus(str, Enum):
    """Status of a background monitoring task."""
    PENDING = "pending"           # Waiting to start polling
    MONITORING = "monitoring"     # Actively polling EMR
    CLUSTER_READY = "cluster_ready"  # Cluster reached target state
    REVERTED = "reverted"         # Param store successfully reverted
    TIMEOUT = "timeout"           # Timed out, auto-reverted
    FAILED = "failed"             # Error occurred
    CANCELLED = "cancelled"       # Manually cancelled


@dataclass
class MonitorTask:
    """Represents a background monitoring task."""
    task_id: str
    cluster_name: str
    request_id: str
    original_config: str
    status: MonitorStatus = MonitorStatus.PENDING
    cluster_id: str | None = None
    cluster_state: str | None = None
    error: str | None = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    reverted: bool = False


class BackgroundClusterMonitor:
    """Manages background monitoring tasks for cluster creation.

    Singleton pattern - only one monitor instance per process.
    Only one optimization can run at a time.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._current_task: MonitorTask | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._initialized = True
        logger.info("[BACKGROUND_MONITOR] Initialized singleton instance")

    @property
    def is_busy(self) -> bool:
        """Check if an optimization is currently in progress."""
        return (self._current_task is not None and
                self._current_task.status in (MonitorStatus.PENDING, MonitorStatus.MONITORING))

    @property
    def current_task(self) -> MonitorTask | None:
        """Get the current or most recent task."""
        return self._current_task

    def start_monitoring(
        self,
        task_id: str,
        cluster_name: str,
        request_id: str,
        original_config: str,
    ) -> MonitorTask:
        """Start a background monitoring task.

        Args:
            task_id: Unique identifier for this optimization
            cluster_name: EMR cluster name to poll for
            request_id: Lambda request ID for user reference
            original_config: Original param store value to restore

        Returns:
            MonitorTask with initial status

        Raises:
            RuntimeError if another optimization is in progress
        """
        if self.is_busy:
            raise RuntimeError(
                f"Another optimization is in progress: {self._current_task.cluster_name}. "
                "Please wait for it to complete or check its status."
            )

        # Create task
        self._current_task = MonitorTask(
            task_id=task_id,
            cluster_name=cluster_name,
            request_id=request_id,
            original_config=original_config,
        )

        logger.info(f"[BACKGROUND_MONITOR] Starting monitor for cluster={cluster_name}, "
                    f"request_id={request_id}, task_id={task_id}")

        audit.info(
            "Background monitor started",
            extra={"audit_data": {
                "event": "background_monitor_start",
                "task_id": task_id,
                "cluster_name": cluster_name,
                "request_id": request_id,
            }},
        )

        # Start background thread
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name=f"cluster-monitor-{cluster_name}",
            daemon=True,
        )
        self._thread.start()

        return self._current_task

    def get_status(self) -> dict:
        """Get current monitoring status for user display."""
        if not self._current_task:
            return {
                "active": False,
                "message": "No optimization in progress.",
            }

        task = self._current_task
        elapsed = (datetime.utcnow() - task.started_at).total_seconds()

        status_messages = {
            MonitorStatus.PENDING: f"Starting to monitor cluster {task.cluster_name}...",
            MonitorStatus.MONITORING: (
                f"Monitoring cluster {task.cluster_name} (request_id: {task.request_id}). "
                f"Current state: {task.cluster_state or 'waiting for cluster'}. "
                f"Elapsed: {int(elapsed)}s. Config will auto-revert when cluster starts."
            ),
            MonitorStatus.CLUSTER_READY: (
                f"Cluster {task.cluster_name} reached state {task.cluster_state}. "
                f"Reverting param store..."
            ),
            MonitorStatus.REVERTED: (
                f"Optimization complete. Cluster {task.cluster_name} (ID: {task.cluster_id}) "
                f"is {task.cluster_state}. Param store reverted successfully."
            ),
            MonitorStatus.TIMEOUT: (
                f"Timed out waiting for cluster {task.cluster_name}. "
                f"Param store was auto-reverted. Please check EMR console."
            ),
            MonitorStatus.FAILED: (
                f"Error monitoring cluster {task.cluster_name}: {task.error}. "
                f"Param store was {'reverted' if task.reverted else 'NOT reverted - manual revert required'}."
            ),
            MonitorStatus.CANCELLED: f"Monitoring cancelled for {task.cluster_name}.",
        }

        return {
            "active": task.status in (MonitorStatus.PENDING, MonitorStatus.MONITORING),
            "task_id": task.task_id,
            "cluster_name": task.cluster_name,
            "request_id": task.request_id,
            "status": task.status.value,
            "cluster_id": task.cluster_id,
            "cluster_state": task.cluster_state,
            "elapsed_seconds": int(elapsed),
            "reverted": task.reverted,
            "message": status_messages.get(task.status, f"Status: {task.status}"),
        }

    def _monitor_loop(self):
        """Background thread: poll EMR and revert when ready."""
        task = self._current_task
        task.status = MonitorStatus.MONITORING

        logger.info(f"[BACKGROUND_MONITOR] Monitor loop started for {task.cluster_name}")

        elapsed = 0
        target_states = ("STARTING", "BOOTSTRAPPING", "RUNNING", "WAITING")
        terminal_states = ("TERMINATED", "TERMINATED_WITH_ERRORS")

        try:
            while elapsed < BACKGROUND_POLL_TIMEOUT and not self._stop_event.is_set():
                # Poll EMR for cluster by name
                cluster_info = self._find_cluster_by_name(task.cluster_name)

                if cluster_info:
                    task.cluster_id = cluster_info["cluster_id"]
                    task.cluster_state = cluster_info["state"]

                    logger.info(f"[BACKGROUND_MONITOR] Cluster {task.cluster_name}: "
                                f"id={task.cluster_id}, state={task.cluster_state}, elapsed={elapsed}s")

                    if task.cluster_state in target_states:
                        logger.info(f"[BACKGROUND_MONITOR] Cluster reached {task.cluster_state}, "
                                    "triggering revert")
                        task.status = MonitorStatus.CLUSTER_READY
                        self._revert_config(task)
                        task.status = MonitorStatus.REVERTED
                        task.completed_at = datetime.utcnow()
                        return

                    if task.cluster_state in terminal_states:
                        logger.error(f"[BACKGROUND_MONITOR] Cluster terminated: {task.cluster_state}")
                        task.error = f"Cluster terminated with state: {task.cluster_state}"
                        task.status = MonitorStatus.FAILED
                        self._revert_config(task)
                        task.completed_at = datetime.utcnow()
                        return
                else:
                    logger.info(f"[BACKGROUND_MONITOR] Cluster {task.cluster_name} not found yet, "
                                f"elapsed={elapsed}s")

                # Wait before next poll
                time.sleep(BACKGROUND_POLL_INTERVAL)
                elapsed += BACKGROUND_POLL_INTERVAL

            # Timeout reached
            logger.warning(f"[BACKGROUND_MONITOR] Timeout after {elapsed}s for {task.cluster_name}")
            task.status = MonitorStatus.TIMEOUT
            task.error = f"Timed out after {elapsed}s waiting for cluster to start"
            self._revert_config(task)
            task.completed_at = datetime.utcnow()

        except Exception as e:
            logger.error(f"[BACKGROUND_MONITOR] Error in monitor loop: {e}")
            task.status = MonitorStatus.FAILED
            task.error = str(e)
            # Try to revert on error
            try:
                self._revert_config(task)
            except Exception as revert_error:
                logger.error(f"[BACKGROUND_MONITOR] Failed to revert after error: {revert_error}")
                task.error += f"; Revert also failed: {revert_error}"
            task.completed_at = datetime.utcnow()

        finally:
            audit.info(
                "Background monitor completed",
                extra={"audit_data": {
                    "event": "background_monitor_complete",
                    "task_id": task.task_id,
                    "cluster_name": task.cluster_name,
                    "status": task.status.value,
                    "cluster_id": task.cluster_id,
                    "cluster_state": task.cluster_state,
                    "reverted": task.reverted,
                    "error": task.error,
                }},
            )

    def _find_cluster_by_name(self, cluster_name: str) -> dict | None:
        """Find a cluster by name, return latest if multiple exist."""
        try:
            from services.emr_service import get_boto3_client

            client = get_boto3_client("emr")

            # List clusters created recently (all states)
            response = client.list_clusters(
                ClusterStates=[
                    "STARTING", "BOOTSTRAPPING", "RUNNING", "WAITING",
                    "TERMINATING", "TERMINATED", "TERMINATED_WITH_ERRORS"
                ]
            )

            # Find cluster by name
            for cluster in response.get("Clusters", []):
                if cluster.get("Name") == cluster_name:
                    return {
                        "cluster_id": cluster["Id"],
                        "state": cluster["Status"]["State"],
                        "name": cluster["Name"],
                    }

            return None

        except Exception as e:
            logger.error(f"[BACKGROUND_MONITOR] Error finding cluster: {e}")
            return None

    def _revert_config(self, task: MonitorTask):
        """Revert param store to original config."""
        try:
            from tools.param_store import revert_param_store

            logger.info(f"[BACKGROUND_MONITOR] Reverting param store for {task.cluster_name}")

            result = revert_param_store.invoke({
                "cluster_name": task.cluster_name,
                "original_value": task.original_config,
            })

            task.reverted = True
            logger.info(f"[BACKGROUND_MONITOR] Revert successful: {result}")

        except Exception as e:
            logger.error(f"[BACKGROUND_MONITOR] Revert failed: {e}")
            task.reverted = False
            raise


# Global singleton instance
monitor = BackgroundClusterMonitor()
