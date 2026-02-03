"""Analysis and recommendation engine for EMR cluster sizing."""

from config import ASYMMETRY_RATIO, HEADROOM_FACTOR, SIZING_THRESHOLDS, get_logger
from services.pricing_service import (
    find_alternatives,
    find_near_miss_alternatives,
    get_cheaper_same_family,
    get_cross_family_recommendations,
    get_instance_spec,
    is_graviton,
)

logger = get_logger(__name__)

# Ordered severity levels for asymmetry gap calculation
_SIZING_LEVELS = ["heavily_oversized", "moderately_oversized", "right_sized", "undersized"]


def _classify_single_dimension(avg: float, peak: float) -> str:
    """Classify a single dimension (CPU or Memory) against thresholds."""
    thresholds = SIZING_THRESHOLDS
    if avg < thresholds["heavily_oversized"]["avg_max"] and peak < thresholds["heavily_oversized"]["peak_max"]:
        return "heavily_oversized"
    elif avg < thresholds["moderately_oversized"]["avg_max"] and peak < thresholds["moderately_oversized"]["peak_max"]:
        return "moderately_oversized"
    elif avg < thresholds["right_sized"]["avg_max"] and peak < thresholds["right_sized"]["peak_max"]:
        return "right_sized"
    else:
        return "undersized"


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


def classify_per_dimension(cpu_avg: float, cpu_p95: float,
                           mem_avg: float, mem_p95: float) -> dict:
    """Classify CPU and Memory sizing independently and detect asymmetry.

    Asymmetry is flagged when:
    - The two dimensions differ by 2+ severity levels, OR
    - One dimension's peak is > ASYMMETRY_RATIO times the other

    Returns:
        dict with cpu_status, mem_status, asymmetric, constraining_dimension
    """
    cpu_status = _classify_single_dimension(cpu_avg, cpu_p95)
    mem_status = _classify_single_dimension(mem_avg, mem_p95)

    cpu_level = _SIZING_LEVELS.index(cpu_status)
    mem_level = _SIZING_LEVELS.index(mem_status)
    level_gap = abs(cpu_level - mem_level)

    # Check ratio-based asymmetry
    peak_cpu = max(cpu_p95, 0.1)
    peak_mem = max(mem_p95, 0.1)
    ratio = max(peak_cpu / peak_mem, peak_mem / peak_cpu)
    ratio_asymmetric = ratio >= ASYMMETRY_RATIO

    asymmetric = level_gap >= 2 or ratio_asymmetric

    # Constraining dimension is the one with higher utilization (closer to undersized)
    if cpu_level > mem_level:
        constraining = "cpu"
    elif mem_level > cpu_level:
        constraining = "memory"
    else:
        constraining = None

    return {
        "cpu_status": cpu_status,
        "mem_status": mem_status,
        "asymmetric": asymmetric,
        "constraining_dimension": constraining,
    }


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
                       required_resources: dict,
                       per_dimension: dict | None = None) -> dict | None:
    """Generate instance type recommendation.

    When sizing_status is right_sized but per_dimension shows asymmetry,
    searches for cheaper alternatives that meet the constraining dimension.

    Returns dict with:
        - recommended_type: instance type string
        - recommendation_kind: 'same_family' | 'cross_family' | 'none_cheaper'
        - arch_change: bool (Graviton <-> Intel switch)
        - current_price: hourly price
        - recommended_price: hourly price
        - savings_percent: percentage savings
        - explanation: str (only for none_cheaper)
    """
    # Undersized without asymmetry: can't optimize, needs more resources
    if sizing_status == "undersized" and (not per_dimension or not per_dimension.get("asymmetric")):
        return None

    current_spec = get_instance_spec(current_type)
    if not current_spec:
        logger.warning(f"Cannot recommend: {current_type} not in catalog")
        return None

    # For right_sized without asymmetry, no change needed
    if sizing_status == "right_sized" and (not per_dimension or not per_dimension.get("asymmetric")):
        return None

    # For undersized+asymmetric: skip same-family downsizing (can't reduce the
    # constraining dimension), go straight to cross-family search for a cheaper
    # family that still meets both requirements (e.g., R-family -> M-family
    # when memory is over-provisioned but CPU is tight).
    if sizing_status != "undersized":
        # Try same-family first (only when not undersized)
        same_family = get_cheaper_same_family(current_type)
        for candidate in same_family:
            if (candidate["vcpu"] >= required_resources["required_vcpu"]
                    and candidate["memory_gb"] >= required_resources["required_memory_gb"]):
                return _build_recommendation(
                    current_type, current_spec,
                    candidate["instance_type"], candidate,
                    "same_family",
                )

    # Cross-family: find cheaper instance meeting both requirements
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

    # No cheaper alternative found — build explanation for asymmetric cases
    if per_dimension and per_dimension.get("asymmetric"):
        explanation = _build_no_cheaper_explanation(
            current_type, current_spec, required_resources
        )
        return {
            "recommended_type": None,
            "recommendation_kind": "none_cheaper",
            "arch_change": False,
            "current_price": current_spec["price_per_hour"],
            "recommended_price": current_spec["price_per_hour"],
            "savings_percent": 0,
            "explanation": explanation,
        }

    logger.info(f"No cheaper alternative found for {current_type}")
    return None


def _build_no_cheaper_explanation(current_type: str, current_spec: dict,
                                  required_resources: dict) -> str:
    """Build a human-readable explanation for why no cheaper alternative exists."""
    near_misses = find_near_miss_alternatives(
        required_resources["required_vcpu"],
        required_resources["required_memory_gb"],
        current_type,
    )

    family = current_spec["family"]
    parts = ["No cheaper alternative found."]

    for nm in near_misses:
        shortfalls = []
        if nm["shortfall_mem"] > 0:
            shortfalls.append(
                f"insufficient memory ({nm['memory_gb']} GB, need "
                f"{required_resources['required_memory_gb']} GB)"
            )
        if nm["shortfall_vcpu"] > 0:
            shortfalls.append(
                f"insufficient vCPU ({nm['vcpu']}, need "
                f"{required_resources['required_vcpu']})"
            )
        if shortfalls:
            parts.append(
                f"{nm['instance_type']} ({nm['vcpu']} vCPU, {nm['memory_gb']} GB, "
                f"${nm['price_per_hour']}/hr) -- {'; '.join(shortfalls)}."
            )

    # Family-level note
    family_base = family.rstrip("gdi0123456789")
    family_desc = {"r": "R-family (memory-optimized)", "m": "M-family (general purpose)",
                   "c": "C-family (compute-optimized)"}.get(family_base, f"{family}-family")
    parts.append(f"{family_desc} already provides best cost ratio for this workload.")

    return "\n".join(parts)


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
                      instance_count: int, metrics: dict,
                      runtime_hours: float | None = None) -> dict:
    """Full analysis pipeline for one node type (CORE or TASK).

    Args:
        node_type: 'CORE' or 'TASK'
        instance_type: Current instance type (e.g., 'r7g.4xlarge')
        instance_count: Number of instances
        metrics: dict with cpu_avg, cpu_p95, mem_avg, mem_p95
        runtime_hours: Cluster runtime in hours (for run cost calculation)

    Returns:
        dict with analysis results including per-dimension breakdown,
        provisioned resources, alternatives, and recommendation
    """
    sizing = classify_sizing(
        metrics["cpu_avg"], metrics["cpu_p95"],
        metrics["mem_avg"], metrics["mem_p95"],
    )
    per_dimension = classify_per_dimension(
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
            "per_dimension": per_dimension,
            "recommendation": None,
            "error": f"Instance type {instance_type} not in catalog",
        }

    required = calculate_required_resources(
        current_spec, metrics["cpu_p95"], metrics["mem_p95"]
    )

    recommendation = recommend_instance(
        instance_type, sizing, profile, required, per_dimension
    )

    # Provisioned resources
    provisioned = {
        "vcpu": current_spec["vcpu"],
        "memory_gb": current_spec["memory_gb"],
    }

    # Current cost
    current_cost_per_hour = current_spec["price_per_hour"]
    fleet_cost_per_hour = current_cost_per_hour * instance_count
    run_cost = fleet_cost_per_hour * runtime_hours if runtime_hours else None

    # Find alternatives (even if right-sized, to show options)
    alternatives = find_alternatives(
        required["required_vcpu"],
        required["required_memory_gb"],
        instance_type,
        current_cost_per_hour,
        max_results=3,
    )
    # Only keep alternatives that are actually cheaper
    alternatives = [a for a in alternatives if a["price_per_hour"] < current_cost_per_hour]

    # Over-provisioning ratios
    cpu_over_ratio = round(provisioned["vcpu"] / max(required["required_vcpu"], 0.1), 1)
    mem_over_ratio = round(provisioned["memory_gb"] / max(required["required_memory_gb"], 0.1), 1)

    # Near-miss explanations when no cheaper alternatives exist and right-sized + asymmetric
    near_misses = None
    if not alternatives and per_dimension["asymmetric"]:
        near_misses = find_near_miss_alternatives(
            required["required_vcpu"],
            required["required_memory_gb"],
            instance_type,
        )

    result = {
        "node_type": node_type,
        "instance_type": instance_type,
        "instance_count": instance_count,
        "metrics": metrics,
        "sizing_status": sizing,
        "per_dimension": per_dimension,
        "workload_profile": profile,
        "required_resources": required,
        "provisioned": provisioned,
        "over_provisioning": {"cpu_ratio": cpu_over_ratio, "mem_ratio": mem_over_ratio},
        "current_cost_per_hour": current_cost_per_hour,
        "fleet_cost_per_hour": fleet_cost_per_hour,
        "run_cost": run_cost,
        "alternatives": alternatives,
        "near_misses": near_misses,
        "recommendation": recommendation,
    }

    return result
