"""Comparable-price math shared by the verdict and the sandbox script."""

from __future__ import annotations


def summarize(price: float, comps: list[float]) -> dict:
    """Return median, mean, z-score, and percent under the median.

    ``pct_under`` is positive when the asking price is below the median.
    """
    if not comps:
        raise ValueError("comps must not be empty")
    ordered = sorted(float(item) for item in comps)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        median = ordered[mid]
    else:
        median = (ordered[mid - 1] + ordered[mid]) / 2
    mean = sum(ordered) / count
    variance = sum((item - mean) ** 2 for item in ordered) / count
    std = variance**0.5 or 1.0
    pct_under = (median - price) / median * 100 if median else 0.0
    return {
        "median": median,
        "mean": mean,
        "zscore": round((price - mean) / std, 3),
        "n": count,
        "listed": price,
        "pct_under": round(pct_under, 1),
    }
