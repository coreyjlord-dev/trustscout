"""Optional local prompt-injection classifier.

``protectai/deberta-v3-base-prompt-injection-v2`` is not an LLM. It stays off
unless ``TRUSTSCOUT_LOCAL_GUARD=1``, so the default demo does not download it.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("trustscout.local_guard")

_PIPELINE = None


def enabled() -> bool:
    """True when the operator turned the local classifier on."""
    return os.environ.get("TRUSTSCOUT_LOCAL_GUARD", "0").strip() == "1"


def score(text: str) -> float | None:
    """Return an injection probability, or None when the classifier is off or missing."""
    if not enabled():
        return None
    try:
        return _model_score(text)
    except Exception as exc:
        logger.warning("local guard unavailable: %s", type(exc).__name__)
        return None


def _model_score(text: str) -> float:
    pipeline = _load()
    result = pipeline(text[:4000])[0]
    label = str(result.get("label", "")).lower()
    value = float(result.get("score", 0.0))
    if "inj" in label:
        return value
    return 1.0 - value


def _load():
    global _PIPELINE
    if _PIPELINE is None:
        from transformers import pipeline

        _PIPELINE = pipeline(
            "text-classification",
            model="protectai/deberta-v3-base-prompt-injection-v2",
            truncation=True,
            max_length=512,
        )
    return _PIPELINE
