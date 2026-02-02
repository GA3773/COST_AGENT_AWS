"""Instance pricing data and catalog for EMR cost calculations."""

from config import GRAVITON_FAMILIES, get_logger

logger = get_logger(__name__)

# Static pricing table: common EMR instance types with on-demand hourly rates (us-east-1)
# Format: {vcpu, memory_gb, price_per_hour, family, arch}
INSTANCE_CATALOG = {
    # M5 - General Purpose (Intel)
    "m5.xlarge":    {"vcpu": 4,  "memory_gb": 16,  "price_per_hour": 0.192, "family": "m5", "arch": "x86_64"},
    "m5.2xlarge":   {"vcpu": 8,  "memory_gb": 32,  "price_per_hour": 0.384, "family": "m5", "arch": "x86_64"},
    "m5.4xlarge":   {"vcpu": 16, "memory_gb": 64,  "price_per_hour": 0.768, "family": "m5", "arch": "x86_64"},
    "m5.8xlarge":   {"vcpu": 32, "memory_gb": 128, "price_per_hour": 1.536, "family": "m5", "arch": "x86_64"},
    "m5.12xlarge":  {"vcpu": 48, "memory_gb": 192, "price_per_hour": 2.304, "family": "m5", "arch": "x86_64"},
    "m5.16xlarge":  {"vcpu": 64, "memory_gb": 256, "price_per_hour": 3.072, "family": "m5", "arch": "x86_64"},
    # M6i - General Purpose (Intel)
    "m6i.xlarge":   {"vcpu": 4,  "memory_gb": 16,  "price_per_hour": 0.192, "family": "m6i", "arch": "x86_64"},
    "m6i.2xlarge":  {"vcpu": 8,  "memory_gb": 32,  "price_per_hour": 0.384, "family": "m6i", "arch": "x86_64"},
    "m6i.4xlarge":  {"vcpu": 16, "memory_gb": 64,  "price_per_hour": 0.768, "family": "m6i", "arch": "x86_64"},
    "m6i.8xlarge":  {"vcpu": 32, "memory_gb": 128, "price_per_hour": 1.536, "family": "m6i", "arch": "x86_64"},
    "m6i.12xlarge": {"vcpu": 48, "memory_gb": 192, "price_per_hour": 2.304, "family": "m6i", "arch": "x86_64"},
    "m6i.16xlarge": {"vcpu": 64, "memory_gb": 256, "price_per_hour": 3.072, "family": "m6i", "arch": "x86_64"},
    # M6g - General Purpose (Graviton)
    "m6g.xlarge":   {"vcpu": 4,  "memory_gb": 16,  "price_per_hour": 0.154, "family": "m6g", "arch": "arm64"},
    "m6g.2xlarge":  {"vcpu": 8,  "memory_gb": 32,  "price_per_hour": 0.308, "family": "m6g", "arch": "arm64"},
    "m6g.4xlarge":  {"vcpu": 16, "memory_gb": 64,  "price_per_hour": 0.616, "family": "m6g", "arch": "arm64"},
    "m6g.8xlarge":  {"vcpu": 32, "memory_gb": 128, "price_per_hour": 1.232, "family": "m6g", "arch": "arm64"},
    "m6g.12xlarge": {"vcpu": 48, "memory_gb": 192, "price_per_hour": 1.848, "family": "m6g", "arch": "arm64"},
    "m6g.16xlarge": {"vcpu": 64, "memory_gb": 256, "price_per_hour": 2.464, "family": "m6g", "arch": "arm64"},
    # M7i - General Purpose (Intel)
    "m7i.xlarge":   {"vcpu": 4,  "memory_gb": 16,  "price_per_hour": 0.202, "family": "m7i", "arch": "x86_64"},
    "m7i.2xlarge":  {"vcpu": 8,  "memory_gb": 32,  "price_per_hour": 0.403, "family": "m7i", "arch": "x86_64"},
    "m7i.4xlarge":  {"vcpu": 16, "memory_gb": 64,  "price_per_hour": 0.806, "family": "m7i", "arch": "x86_64"},
    "m7i.8xlarge":  {"vcpu": 32, "memory_gb": 128, "price_per_hour": 1.613, "family": "m7i", "arch": "x86_64"},
    "m7i.12xlarge": {"vcpu": 48, "memory_gb": 192, "price_per_hour": 2.419, "family": "m7i", "arch": "x86_64"},
    "m7i.16xlarge": {"vcpu": 64, "memory_gb": 256, "price_per_hour": 3.226, "family": "m7i", "arch": "x86_64"},
    # M7g - General Purpose (Graviton)
    "m7g.xlarge":   {"vcpu": 4,  "memory_gb": 16,  "price_per_hour": 0.163, "family": "m7g", "arch": "arm64"},
    "m7g.2xlarge":  {"vcpu": 8,  "memory_gb": 32,  "price_per_hour": 0.326, "family": "m7g", "arch": "arm64"},
    "m7g.4xlarge":  {"vcpu": 16, "memory_gb": 64,  "price_per_hour": 0.653, "family": "m7g", "arch": "arm64"},
    "m7g.8xlarge":  {"vcpu": 32, "memory_gb": 128, "price_per_hour": 1.306, "family": "m7g", "arch": "arm64"},
    "m7g.12xlarge": {"vcpu": 48, "memory_gb": 192, "price_per_hour": 1.958, "family": "m7g", "arch": "arm64"},
    "m7g.16xlarge": {"vcpu": 64, "memory_gb": 256, "price_per_hour": 2.611, "family": "m7g", "arch": "arm64"},
    # R5 - Memory Optimized (Intel)
    "r5.xlarge":    {"vcpu": 4,  "memory_gb": 32,  "price_per_hour": 0.252, "family": "r5", "arch": "x86_64"},
    "r5.2xlarge":   {"vcpu": 8,  "memory_gb": 64,  "price_per_hour": 0.504, "family": "r5", "arch": "x86_64"},
    "r5.4xlarge":   {"vcpu": 16, "memory_gb": 128, "price_per_hour": 1.008, "family": "r5", "arch": "x86_64"},
    "r5.8xlarge":   {"vcpu": 32, "memory_gb": 256, "price_per_hour": 2.016, "family": "r5", "arch": "x86_64"},
    "r5.12xlarge":  {"vcpu": 48, "memory_gb": 384, "price_per_hour": 3.024, "family": "r5", "arch": "x86_64"},
    "r5.16xlarge":  {"vcpu": 64, "memory_gb": 512, "price_per_hour": 4.032, "family": "r5", "arch": "x86_64"},
    # R6i - Memory Optimized (Intel)
    "r6i.xlarge":   {"vcpu": 4,  "memory_gb": 32,  "price_per_hour": 0.252, "family": "r6i", "arch": "x86_64"},
    "r6i.2xlarge":  {"vcpu": 8,  "memory_gb": 64,  "price_per_hour": 0.504, "family": "r6i", "arch": "x86_64"},
    "r6i.4xlarge":  {"vcpu": 16, "memory_gb": 128, "price_per_hour": 1.008, "family": "r6i", "arch": "x86_64"},
    "r6i.8xlarge":  {"vcpu": 32, "memory_gb": 256, "price_per_hour": 2.016, "family": "r6i", "arch": "x86_64"},
    "r6i.12xlarge": {"vcpu": 48, "memory_gb": 384, "price_per_hour": 3.024, "family": "r6i", "arch": "x86_64"},
    "r6i.16xlarge": {"vcpu": 64, "memory_gb": 512, "price_per_hour": 4.032, "family": "r6i", "arch": "x86_64"},
    # R6g - Memory Optimized (Graviton)
    "r6g.xlarge":   {"vcpu": 4,  "memory_gb": 32,  "price_per_hour": 0.201, "family": "r6g", "arch": "arm64"},
    "r6g.2xlarge":  {"vcpu": 8,  "memory_gb": 64,  "price_per_hour": 0.403, "family": "r6g", "arch": "arm64"},
    "r6g.4xlarge":  {"vcpu": 16, "memory_gb": 128, "price_per_hour": 0.806, "family": "r6g", "arch": "arm64"},
    "r6g.8xlarge":  {"vcpu": 32, "memory_gb": 256, "price_per_hour": 1.613, "family": "r6g", "arch": "arm64"},
    "r6g.12xlarge": {"vcpu": 48, "memory_gb": 384, "price_per_hour": 2.419, "family": "r6g", "arch": "arm64"},
    "r6g.16xlarge": {"vcpu": 64, "memory_gb": 512, "price_per_hour": 3.226, "family": "r6g", "arch": "arm64"},
    # R7i - Memory Optimized (Intel)
    "r7i.xlarge":   {"vcpu": 4,  "memory_gb": 32,  "price_per_hour": 0.265, "family": "r7i", "arch": "x86_64"},
    "r7i.2xlarge":  {"vcpu": 8,  "memory_gb": 64,  "price_per_hour": 0.530, "family": "r7i", "arch": "x86_64"},
    "r7i.4xlarge":  {"vcpu": 16, "memory_gb": 128, "price_per_hour": 1.059, "family": "r7i", "arch": "x86_64"},
    "r7i.8xlarge":  {"vcpu": 32, "memory_gb": 256, "price_per_hour": 2.118, "family": "r7i", "arch": "x86_64"},
    "r7i.12xlarge": {"vcpu": 48, "memory_gb": 384, "price_per_hour": 3.178, "family": "r7i", "arch": "x86_64"},
    "r7i.16xlarge": {"vcpu": 64, "memory_gb": 512, "price_per_hour": 4.237, "family": "r7i", "arch": "x86_64"},
    # R7g - Memory Optimized (Graviton)
    "r7g.xlarge":   {"vcpu": 4,  "memory_gb": 32,  "price_per_hour": 0.214, "family": "r7g", "arch": "arm64"},
    "r7g.2xlarge":  {"vcpu": 8,  "memory_gb": 64,  "price_per_hour": 0.428, "family": "r7g", "arch": "arm64"},
    "r7g.4xlarge":  {"vcpu": 16, "memory_gb": 128, "price_per_hour": 0.857, "family": "r7g", "arch": "arm64"},
    "r7g.8xlarge":  {"vcpu": 32, "memory_gb": 256, "price_per_hour": 1.714, "family": "r7g", "arch": "arm64"},
    "r7g.12xlarge": {"vcpu": 48, "memory_gb": 384, "price_per_hour": 2.570, "family": "r7g", "arch": "arm64"},
    "r7g.16xlarge": {"vcpu": 64, "memory_gb": 512, "price_per_hour": 3.427, "family": "r7g", "arch": "arm64"},
    # C5 - Compute Optimized (Intel)
    "c5.xlarge":    {"vcpu": 4,  "memory_gb": 8,   "price_per_hour": 0.170, "family": "c5", "arch": "x86_64"},
    "c5.2xlarge":   {"vcpu": 8,  "memory_gb": 16,  "price_per_hour": 0.340, "family": "c5", "arch": "x86_64"},
    "c5.4xlarge":   {"vcpu": 16, "memory_gb": 32,  "price_per_hour": 0.680, "family": "c5", "arch": "x86_64"},
    "c5.9xlarge":   {"vcpu": 36, "memory_gb": 72,  "price_per_hour": 1.530, "family": "c5", "arch": "x86_64"},
    "c5.12xlarge":  {"vcpu": 48, "memory_gb": 96,  "price_per_hour": 2.040, "family": "c5", "arch": "x86_64"},
    # C6i - Compute Optimized (Intel)
    "c6i.xlarge":   {"vcpu": 4,  "memory_gb": 8,   "price_per_hour": 0.170, "family": "c6i", "arch": "x86_64"},
    "c6i.2xlarge":  {"vcpu": 8,  "memory_gb": 16,  "price_per_hour": 0.340, "family": "c6i", "arch": "x86_64"},
    "c6i.4xlarge":  {"vcpu": 16, "memory_gb": 32,  "price_per_hour": 0.680, "family": "c6i", "arch": "x86_64"},
    "c6i.8xlarge":  {"vcpu": 32, "memory_gb": 64,  "price_per_hour": 1.360, "family": "c6i", "arch": "x86_64"},
    "c6i.12xlarge": {"vcpu": 48, "memory_gb": 96,  "price_per_hour": 2.040, "family": "c6i", "arch": "x86_64"},
    "c6i.16xlarge": {"vcpu": 64, "memory_gb": 128, "price_per_hour": 2.720, "family": "c6i", "arch": "x86_64"},
    # C6g - Compute Optimized (Graviton)
    "c6g.xlarge":   {"vcpu": 4,  "memory_gb": 8,   "price_per_hour": 0.136, "family": "c6g", "arch": "arm64"},
    "c6g.2xlarge":  {"vcpu": 8,  "memory_gb": 16,  "price_per_hour": 0.272, "family": "c6g", "arch": "arm64"},
    "c6g.4xlarge":  {"vcpu": 16, "memory_gb": 32,  "price_per_hour": 0.544, "family": "c6g", "arch": "arm64"},
    "c6g.8xlarge":  {"vcpu": 32, "memory_gb": 64,  "price_per_hour": 1.088, "family": "c6g", "arch": "arm64"},
    "c6g.12xlarge": {"vcpu": 48, "memory_gb": 96,  "price_per_hour": 1.632, "family": "c6g", "arch": "arm64"},
    "c6g.16xlarge": {"vcpu": 64, "memory_gb": 128, "price_per_hour": 2.176, "family": "c6g", "arch": "arm64"},
    # C7i - Compute Optimized (Intel)
    "c7i.xlarge":   {"vcpu": 4,  "memory_gb": 8,   "price_per_hour": 0.179, "family": "c7i", "arch": "x86_64"},
    "c7i.2xlarge":  {"vcpu": 8,  "memory_gb": 16,  "price_per_hour": 0.357, "family": "c7i", "arch": "x86_64"},
    "c7i.4xlarge":  {"vcpu": 16, "memory_gb": 32,  "price_per_hour": 0.714, "family": "c7i", "arch": "x86_64"},
    "c7i.8xlarge":  {"vcpu": 32, "memory_gb": 64,  "price_per_hour": 1.428, "family": "c7i", "arch": "x86_64"},
    "c7i.12xlarge": {"vcpu": 48, "memory_gb": 96,  "price_per_hour": 2.142, "family": "c7i", "arch": "x86_64"},
    "c7i.16xlarge": {"vcpu": 64, "memory_gb": 128, "price_per_hour": 2.856, "family": "c7i", "arch": "x86_64"},
    # C7g - Compute Optimized (Graviton)
    "c7g.xlarge":   {"vcpu": 4,  "memory_gb": 8,   "price_per_hour": 0.145, "family": "c7g", "arch": "arm64"},
    "c7g.2xlarge":  {"vcpu": 8,  "memory_gb": 16,  "price_per_hour": 0.289, "family": "c7g", "arch": "arm64"},
    "c7g.4xlarge":  {"vcpu": 16, "memory_gb": 32,  "price_per_hour": 0.578, "family": "c7g", "arch": "arm64"},
    "c7g.8xlarge":  {"vcpu": 32, "memory_gb": 64,  "price_per_hour": 1.156, "family": "c7g", "arch": "arm64"},
    "c7g.12xlarge": {"vcpu": 48, "memory_gb": 96,  "price_per_hour": 1.734, "family": "c7g", "arch": "arm64"},
    "c7g.16xlarge": {"vcpu": 64, "memory_gb": 128, "price_per_hour": 2.312, "family": "c7g", "arch": "arm64"},
}

# Size ordering for same-family downsizing
SIZE_ORDER = ["xlarge", "2xlarge", "4xlarge", "8xlarge", "9xlarge", "12xlarge", "16xlarge"]


def is_graviton(instance_type: str) -> bool:
    """Check if an instance type uses Graviton (ARM) architecture."""
    family = instance_type.split(".")[0]
    return family in GRAVITON_FAMILIES


def get_instance_spec(instance_type: str) -> dict | None:
    """Look up instance specifications from catalog."""
    spec = INSTANCE_CATALOG.get(instance_type)
    if not spec:
        logger.warning(f"Instance type {instance_type} not found in catalog")
    return spec


def get_cheaper_same_family(instance_type: str) -> list[dict]:
    """Get smaller instances in the same family, ordered by price ascending."""
    spec = get_instance_spec(instance_type)
    if not spec:
        return []

    family = spec["family"]
    current_size = instance_type.split(".")[1]

    try:
        current_idx = SIZE_ORDER.index(current_size)
    except ValueError:
        return []

    candidates = []
    for itype, ispec in INSTANCE_CATALOG.items():
        if ispec["family"] != family:
            continue
        size = itype.split(".")[1]
        try:
            size_idx = SIZE_ORDER.index(size)
        except ValueError:
            continue
        if size_idx < current_idx:
            candidates.append({"instance_type": itype, **ispec})

    return sorted(candidates, key=lambda x: x["price_per_hour"])


def get_cross_family_recommendations(required_vcpu: float, required_mem: float,
                                     workload_profile: str) -> list[dict]:
    """Find cheapest instances across all families that meet resource requirements.

    Args:
        required_vcpu: Minimum vCPU count needed (with headroom)
        required_mem: Minimum memory in GB needed (with headroom)
        workload_profile: 'cpu_heavy', 'memory_heavy', or 'balanced'

    Returns:
        List of candidates sorted by price, cheapest first
    """
    candidates = []
    for itype, spec in INSTANCE_CATALOG.items():
        if spec["vcpu"] >= required_vcpu and spec["memory_gb"] >= required_mem:
            # Prefer matching workload profile
            profile_match = _matches_profile(spec["family"], workload_profile)
            candidates.append({
                "instance_type": itype,
                "profile_match": profile_match,
                **spec,
            })

    # Sort: profile match first, then by price
    return sorted(candidates, key=lambda x: (not x["profile_match"], x["price_per_hour"]))


def _matches_profile(family: str, workload_profile: str) -> bool:
    """Check if instance family matches the workload profile."""
    family_base = family.rstrip("gdi0123456789")
    if workload_profile == "cpu_heavy":
        return family_base == "c"
    elif workload_profile == "memory_heavy":
        return family_base == "r"
    else:  # balanced
        return family_base == "m"


def refresh_from_aws_pricing_api():
    """Optional: refresh pricing data from AWS Pricing API.

    This is best-effort and silently fails if the API is unavailable.
    """
    try:
        client = get_boto3_client("pricing")
        # The pricing API is only available in us-east-1 and ap-south-1
        # This is a placeholder for future implementation
        logger.info("Pricing API refresh not yet implemented, using static catalog")
    except Exception as e:
        logger.warning(f"Could not refresh pricing from AWS API: {e}")
