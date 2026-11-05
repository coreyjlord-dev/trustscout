"""Turn listing signals into a risk band. The model writes the summary, not the score."""

from __future__ import annotations

from dataclasses import dataclass

WEIGHTS = {
    "seller_mismatch": 60,
    "recycled_photo": 60,
    "price_far_below": 55,
    "injection": 60,
    "hostile_payload": 60,
    "vision_mismatch": 30,
    "comps_failed": 10,
}


@dataclass
class Signal:
    """One reason on the verdict card."""

    code: str
    severity: str
    text: str
    artifact_kind: str


def score_of(signals: list[Signal]) -> int:
    """Score from 0 to 99. Info signals do not add weight."""
    total = 8
    for signal in signals:
        total += WEIGHTS.get(signal.code, 0)
    return max(0, min(99, total))


def band(score: int) -> str:
    """Map a score to low, medium, or high."""
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def price_signal(stats: dict) -> Signal:
    """Describe how the asking price sits against the median."""
    pct = float(stats["pct_under"])
    median = float(stats["median"])
    listed = float(stats["listed"])
    zscore = stats["zscore"]
    if listed <= median * 0.45:
        text = (
            f"Asking ${listed:.0f} is {pct:.0f}% under the median ${median:.0f} "
            f"(z={zscore}). That gap is large enough to treat as a scam price."
        )
        return Signal("price_far_below", "high", text, "chart")
    if pct >= 10:
        text = (
            f"Asking ${listed:.0f} is {pct:.0f}% under the median ${median:.0f} "
            f"(z={zscore}). The discount is plausible."
        )
        return Signal("price_under", "info", text, "chart")
    if listed > median * 1.25:
        text = f"Asking ${listed:.0f} is above the median ${median:.0f} (z={zscore})."
        return Signal("price_high", "warn", text, "chart")
    text = f"Asking ${listed:.0f} is close to the median ${median:.0f} (z={zscore})."
    return Signal("price_fair", "info", text, "chart")
