"""Influence-decay math, straight from the rules.

    final_rate (IP/min) = BASE_TAX * (1 + influence_tax) * (1 + region_tax)
"""

from ..config import settings


def influence_tax(ip: float) -> float:
    """Extra decay multiplier by how much IP a team holds (rule 1 table)."""
    if ip > 5000:
        return 5.0   # 500%
    if ip > 2000:
        return 1.0   # 100%
    if ip > 1000:
        return 0.5   # 50%
    if ip > 500:
        return 0.1   # 10%
    return 0.0


def compute_rate(ip: float, region_tax: float) -> float:
    """IP lost per minute. region_tax is e.g. 0.70 for Rīga, 0.0 if not in a region."""
    return settings.base_tax * (1.0 + influence_tax(ip)) * (1.0 + region_tax)
