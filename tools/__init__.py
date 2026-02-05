"""LangGraph tool wrappers for EMR cost optimization."""

from tools.analyze import analyze_cluster
from tools.cost_calculator import calculate_cost
from tools.emr_operations import check_cluster_status, list_transient_clusters
from tools.lambda_operations import invoke_cluster_lambda
from tools.metrics import collect_node_metrics
from tools.optimization_status import check_optimization_status
from tools.param_store import (
    get_param_store_config,
    modify_param_store,
    revert_param_store,
)

ALL_TOOLS = [
    list_transient_clusters,
    analyze_cluster,
    collect_node_metrics,
    get_param_store_config,
    modify_param_store,
    revert_param_store,
    invoke_cluster_lambda,
    check_cluster_status,
    check_optimization_status,
    calculate_cost,
]
