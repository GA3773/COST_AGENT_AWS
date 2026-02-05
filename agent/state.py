"""Agent state schema for LangGraph workflow."""

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """State schema for the EMR cost optimization agent."""

    # Conversation
    messages: Annotated[list, add_messages]

    # Cluster identification
    cluster_name: Optional[str]
    cluster_id: Optional[str]

    # Parameter Store
    param_store_config: Optional[dict]
    original_config_backup: Optional[str]  # raw string value for exact revert

    # Per-node analysis
    core_analysis: Optional[dict]
    task_analysis: Optional[dict]

    # Per-node recommendations
    core_recommendation: Optional[dict]
    task_recommendation: Optional[dict]

    # Cost
    estimated_savings: Optional[dict]

    # Approval
    human_approved: Optional[bool]

    # Execution
    modified_config: Optional[dict]
    new_cluster_id: Optional[str]
    new_cluster_status: Optional[str]
    config_reverted: Optional[bool]

    # Background monitoring
    optimization_request_id: Optional[str]  # Lambda request ID
    optimization_task_id: Optional[str]     # Background monitor task ID
    optimization_status: Optional[str]      # pending/monitoring/reverted/failed

    # Output
    final_report: Optional[str]

    # Tracking
    correlation_id: Optional[str]
    current_phase: Optional[str]
    error: Optional[str]
