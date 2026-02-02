"""Cost calculation tool for comparing current vs recommended configurations."""

from langchain_core.tools import tool

from services.pricing_service import get_instance_spec


@tool
def calculate_cost(current_core_type: str, current_core_count: int,
                   current_task_type: str, current_task_count: int,
                   recommended_core_type: str, recommended_task_type: str,
                   runtime_hours: float) -> str:
    """Calculate cost comparison between current and recommended instance configurations.

    Args:
        current_core_type: Current CORE instance type
        current_core_count: Number of CORE instances
        current_task_type: Current TASK instance type
        current_task_count: Number of TASK instances
        recommended_core_type: Recommended CORE instance type (same as current if no change)
        recommended_task_type: Recommended TASK instance type (same as current if no change)
        runtime_hours: Cluster runtime in hours

    Returns:
        Formatted cost comparison with per-run and monthly estimates.
    """
    # Get pricing
    core_current = get_instance_spec(current_core_type)
    core_rec = get_instance_spec(recommended_core_type)
    task_current = get_instance_spec(current_task_type)
    task_rec = get_instance_spec(recommended_task_type)

    if not all([core_current, core_rec, task_current, task_rec]):
        return "Error: One or more instance types not found in pricing catalog."

    # Per-run cost
    current_cost = (
        core_current["price_per_hour"] * current_core_count * runtime_hours
        + task_current["price_per_hour"] * current_task_count * runtime_hours
    )
    recommended_cost = (
        core_rec["price_per_hour"] * current_core_count * runtime_hours
        + task_rec["price_per_hour"] * current_task_count * runtime_hours
    )

    savings_per_run = current_cost - recommended_cost
    savings_pct = (savings_per_run / current_cost * 100) if current_cost > 0 else 0

    # Estimate monthly (assume 30 runs/month for transient clusters)
    runs_per_month = 30
    monthly_current = current_cost * runs_per_month
    monthly_recommended = recommended_cost * runs_per_month
    monthly_savings = savings_per_run * runs_per_month

    # Test cluster cost estimate (single run with recommended config)
    test_cost = recommended_cost

    lines = [
        "Cost Comparison",
        "=" * 40,
        "",
        f"Per Run ({runtime_hours}h):",
        f"  Current:     ${current_cost:,.2f}",
        f"  Recommended: ${recommended_cost:,.2f}",
        f"  Savings:     ${savings_per_run:,.2f} ({savings_pct:.1f}%)",
        "",
        f"Monthly Estimate ({runs_per_month} runs):",
        f"  Current:     ${monthly_current:,.2f}",
        f"  Recommended: ${monthly_recommended:,.2f}",
        f"  Savings:     ${monthly_savings:,.2f}",
        "",
        f"Test cluster cost: ~${test_cost:,.2f}",
    ]

    return "\n".join(lines)
