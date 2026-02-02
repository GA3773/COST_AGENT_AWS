"""Analysis and recommendation engine for EMR cluster sizing."""

from config import HEADROOM_FACTOR, SIZING_THRESHOLDS, get_logger
from services.pricing_service import (
    get_cheaper_same_family,
    get_cross_family_recommendations,
    get_instance_spec,
    is_graviton,
)

logger = get_logger(__name__)


def classify_sizing(cpu_avg: float, cpu_p95: float,
                    mem_avg: float, mem_p95: float) -> str:
    """Classify node sizing status using the HIGHER of CPU and Memory utilization.

    Returns one of: heavily_oversized, moderately_oversized, right_sized, undersized
    """
    # Use the higher utilization metric
    avg = max(cpu_avg, mem_avg)
    peak = max(cpu_p95, mem_p95)

    thresholds = SIZING_THRESHOLDS
    if avg < thresholds["heavily_oversized"]["avg_max"] and peak < thresholds["heavily_oversized"]["peak_max"]:
        return "heavily_oversized"
    elif avg < thresholds["moderately_oversized"]["avg_max"] and peak < thresholds["moderately_oversized"]["peak_max"]:
        return "moderately_oversized"
    elif avg < thresholds["right_sized"]["avg_max"] and peak < thresholds["right_sized"]["peak_max"]:
        return "right_sized"
    else:
        return "undersized"


def detect_workload_profile(cpu_avg: float, mem_avg: float) -> str:
    """Detect workload type based on CPU vs memory utilization ratio.

    Returns: 'cpu_heavy', 'memory_heavy', or 'balanced'
    """
    if cpu_avg == 0 and mem_avg == 0:
        return "balanced"

    if mem_avg > 0 and cpu_avg / max(mem_avg, 0.1) > 1.5:
        return "cpu_heavy"
    elif cpu_avg > 0 and mem_avg / max(cpu_avg, 0.1) > 1.5:
        return "memory_heavy"
    else:
        return "balanced"


def calculate_required_resources(current_spec: dict, peak_cpu: float,
                                 peak_mem: float) -> dict:
    """Calculate required resources with headroom factor.

    Args:
        current_spec: Current instance spec from catalog
        peak_cpu: P95 CPU utilization percentage
        peak_mem: P95 memory utilization percentage

    Returns:
        dict with required_vcpu and required_memory_gb
    """
    required_vcpu = (current_spec["vcpu"] * (peak_cpu / 100.0)) * HEADROOM_FACTOR
    required_mem = (current_spec["memory_gb"] * (peak_mem / 100.0)) * HEADROOM_FACTOR

    return {
        "required_vcpu": round(required_vcpu, 1),
        "required_memory_gb": round(required_mem, 1),
    }


def recommend_instance(current_type: str, sizing_status: str,
                       workload_profile: str,
                       required_resources: dict) -> dict | None:
    """Generate instance type recommendation.

    Returns dict with:
        - recommended_type: instance type string
        - recommendation_kind: 'same_family' | 'cross_family'
        - arch_change: bool (Graviton <-> Intel switch)
        - current_price: hourly price
        - recommended_price: hourly price
        - savings_percent: percentage savings
    """
    if sizing_status == "right_sized":
        return None  # No change needed
    if sizing_status == "undersized":
        return None  # Upsizing is out of scope for cost optimization

    current_spec = get_instance_spec(current_type)
    if not current_spec:
        logger.warning(f"Cannot recommend: {current_type} not in catalog")
        return None

    # Try same-family first
    same_family = get_cheaper_same_family(current_type)
    for candidate in same_family:
        if (candidate["vcpu"] >= required_resources["required_vcpu"]
                and candidate["memory_gb"] >= required_resources["required_memory_gb"]):
            return _build_recommendation(
                current_type, current_spec,
                candidate["instance_type"], candidate,
                "same_family",
            )

    # Fall back to cross-family
    cross_family = get_cross_family_recommendations(
        required_resources["required_vcpu"],
        required_resources["required_memory_gb"],
        workload_profile,
    )
    for candidate in cross_family:
        if candidate["price_per_hour"] < current_spec["price_per_hour"]:
            return _build_recommendation(
                current_type, current_spec,
                candidate["instance_type"], candidate,
                "cross_family",
            )

    logger.info(f"No cheaper alternative found for {current_type}")
    return None


def _build_recommendation(current_type: str, current_spec: dict,
                          rec_type: str, rec_spec: dict,
                          kind: str) -> dict:
    """Build a recommendation result dict."""
    current_graviton = is_graviton(current_type)
    rec_graviton = is_graviton(rec_type)
    savings_pct = ((current_spec["price_per_hour"] - rec_spec["price_per_hour"])
                   / current_spec["price_per_hour"] * 100)

    return {
        "recommended_type": rec_type,
        "recommendation_kind": kind,
        "arch_change": current_graviton != rec_graviton,
        "current_arch": "arm64" if current_graviton else "x86_64",
        "recommended_arch": "arm64" if rec_graviton else "x86_64",
        "current_price": current_spec["price_per_hour"],
        "recommended_price": rec_spec["price_per_hour"],
        "savings_percent": round(savings_pct, 1),
    }


def analyze_node_type(node_type: str, instance_type: str,
                      instance_count: int, metrics: dict) -> dict:
    """Full analysis pipeline for one node type (CORE or TASK).

    Args:
        node_type: 'CORE' or 'TASK'
        instance_type: Current instance type (e.g., 'r7g.4xlarge')
        instance_count: Number of instances
        metrics: dict with cpu_avg, cpu_p95, mem_avg, mem_p95

    Returns:
        dict with analysis results and recommendation
    """
    sizing = classify_sizing(
        metrics["cpu_avg"], metrics["cpu_p95"],
        metrics["mem_avg"], metrics["mem_p95"],
    )
    profile = detect_workload_profile(metrics["cpu_avg"], metrics["mem_avg"])

    current_spec = get_instance_spec(instance_type)
    if not current_spec:
        return {
            "node_type": node_type,
            "instance_type": instance_type,
            "instance_count": instance_count,
            "metrics": metrics,
            "sizing_status": "unknown",
            "workload_profile": profile,
            "recommendation": None,
            "error": f"Instance type {instance_type} not in catalog",
        }

    required = calculate_required_resources(
        current_spec, metrics["cpu_p95"], metrics["mem_p95"]
    )

    recommendation = recommend_instance(
        instance_type, sizing, profile, required
    )

    return {
        "node_type": node_type,
        "instance_type": instance_type,
        "instance_count": instance_count,
        "metrics": metrics,
        "sizing_status": sizing,
        "workload_profile": profile,
        "required_resources": required,
        "recommendation": recommendation,
    }
