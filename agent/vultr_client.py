"""Vultr Serverless Inference client, plus a deterministic offline mode.

Live mode is used only when ``VULTR_INFERENCE_API_KEY`` is set and
``TRUSTSCOUT_MODE`` is not ``mock``. Mock mode never opens a socket.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass

from agent.heuristics import injection_hits, strip_hostile

logger = logging.getLogger("trustscout.inference")

VULTR_BASE_URL = "https://api.vultrinference.com/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash-1017"

MODEL_PLANNER = "qwen3.8-flash-next"
MODEL_PLANNER_FALLBACK = "glm-5.3-flash"
MODEL_VISION = "qwen3.8-flash-next"
MODEL_VISION_FALLBACK = "glm-5.3-flash"
MODEL_GUARD = "nemotron-3.5-content-safety"
MODEL_VERDICT = "glm-5.3"
MODEL_VERDICT_FALLBACK = "mimo-v2.6-pro"
MODEL_COMPS = "qwen3.8-flash-next"
MODEL_COMPS_FALLBACK = "glm-5.3-flash"

FALLBACKS = {
    MODEL_PLANNER: MODEL_PLANNER_FALLBACK,
    MODEL_VISION: MODEL_VISION_FALLBACK,
    MODEL_VERDICT: MODEL_VERDICT_FALLBACK,
    MODEL_COMPS: MODEL_COMPS_FALLBACK,
}

# Recorded prompts exist only when tests opt in. Production logs never store them.
RECORDED: list[str] = []


class InferenceError(RuntimeError):
    """A model call failed, or the response model id did not match the request."""


@dataclass
class ChatResult:
    """One completion after the model-id check."""

    model: str
    content: str
    mode: str


def inference_mode() -> str:
    """Return ``live`` or ``mock``.

    A set ``VULTR_INFERENCE_API_KEY`` selects live mode unless ``TRUSTSCOUT_MODE=mock``.
    """
    forced = os.environ.get("TRUSTSCOUT_MODE", "").strip().lower()
    key = os.environ.get("VULTR_INFERENCE_API_KEY", "").strip()
    if forced == "mock":
        return "mock"
    if forced == "live" and not key:
        raise InferenceError("TRUSTSCOUT_MODE=live but VULTR_INFERENCE_API_KEY is empty")
    if key:
        return "live"
    return "mock"


def chat(model: str, messages: list[dict], *, temperature: float = 0.0) -> ChatResult:
    """Send one chat completion through Vultr, or the local fixture table."""
    if os.environ.get("TRUSTSCOUT_RECORD_PROMPTS") == "1":
        RECORDED.append(_flatten(messages))
    mode = inference_mode()
    if mode == "mock":
        content = mock_complete(model, messages)
        assert_model(model, model)
        logger.info("inference model=%s mode=mock", model)
        return ChatResult(model=model, content=content, mode="mock")
    return _live_chat(model, messages, temperature=temperature)


def assert_model(requested: str, returned: str) -> None:
    """Reject Vultr's silent fallback to the default model."""
    if not returned:
        raise InferenceError(f"empty model field for requested {requested}")
    if returned == DEFAULT_MODEL and requested != DEFAULT_MODEL:
        raise InferenceError(
            f"model mismatch: requested {requested}, response said {returned}. "
            "An unknown id is served as the default model."
        )
    if returned != requested and not returned.endswith(requested):
        raise InferenceError(f"model mismatch: requested {requested}, response said {returned}")


def parse_json(content: str) -> dict:
    """Parse a JSON object, including a fenced block."""
    text = content.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise InferenceError("model JSON was not an object")
    return data


def mock_complete(model: str, messages: list[dict]) -> str:
    """Return the fixture for the task tag in the system message."""
    blob = _flatten(messages)
    task = _task_name(blob)
    if task == "guard":
        return json.dumps(_mock_guard(blob))
    if task == "plan":
        return json.dumps(
            {
                "steps": ["browser", "guard", "vision", "comps", "verdict"],
                "note": "The control plane runs this fixed pipeline.",
            }
        )
    if task == "vision":
        return json.dumps(_mock_vision(blob))
    if task == "comps":
        from agent.comps import COMPS_SCRIPT

        if "FORCE_BAD_CODE" in blob and "PREVIOUS_STDERR" not in blob:
            return "this is not python ("
        return COMPS_SCRIPT
    if task == "verdict":
        return json.dumps(_mock_verdict(blob))
    raise InferenceError(f"no mock fixture for model {model}")


def _mock_guard(blob: str) -> dict:
    hits = injection_hits(blob)
    return {
        "flagged": bool(hits),
        "hits": hits,
        "stripped": strip_hostile(blob),
    }


def _mock_vision(blob: str) -> dict:
    dom = _embedded_json(blob, "DOM_JSON")
    price = _price_number(str(dom.get("price", "")))
    return {
        "title": dom.get("title") or "",
        "price": price,
        "seller": dom.get("seller") or "",
        "item_id": dom.get("item_id") or "",
        "condition": dom.get("condition") or "",
        "notes": ["Printed price matches the price block."],
    }


def _mock_verdict(blob: str) -> dict:
    payload = _embedded_json(blob, "SIGNAL_JSON")
    risk = str(payload.get("risk") or "low")
    summaries = {
        "low": "The asking price sits near recent comps and the seller block matches the profile.",
        "medium": "Some claims need a second look before anyone pays.",
        "high": "Do not pay. The page has a scam or hostile signal, and the receipts are linked below.",
    }
    return {"risk": risk, "summary": summaries.get(risk, summaries["medium"])}


def _embedded_json(blob: str, marker: str) -> dict:
    token = marker + ":"
    start = blob.find(token)
    if start < 0:
        return {}
    raw = blob[start + len(token) :].strip()
    try:
        data, _end = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _price_number(text: str) -> float | None:
    digits = "".join(ch if ch.isdigit() or ch == "." else " " for ch in text)
    parts = [part for part in digits.split() if part]
    if not parts:
        return None
    try:
        return float(parts[0])
    except ValueError:
        return None


def _task_name(blob: str) -> str:
    for line in blob.splitlines():
        if line.lower().startswith("trustscout-task:"):
            return line.split(":", 1)[1].strip().lower()
    return ""


def _flatten(messages: list[dict]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def _live_chat(model: str, messages: list[dict], *, temperature: float) -> ChatResult:
    from openai import OpenAI

    client = OpenAI(
        base_url=VULTR_BASE_URL,
        api_key=os.environ["VULTR_INFERENCE_API_KEY"],
    )
    candidates = [model]
    fallback = FALLBACKS.get(model)
    if fallback:
        candidates.append(fallback)
    last_error: Exception | None = None
    for candidate in candidates:
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=candidate,
                    messages=messages,
                    temperature=temperature,
                )
                returned = response.model or ""
                assert_model(candidate, returned)
                content = response.choices[0].message.content or ""
                logger.info("inference model=%s mode=live", returned)
                return ChatResult(model=returned, content=content, mode="live")
            except InferenceError:
                logger.warning("model mismatch for %s; trying the fallback", candidate)
                last_error = InferenceError(f"model mismatch for {candidate}")
                break
            except Exception as exc:
                last_error = exc
                logger.warning("inference error model=%s attempt=%s", candidate, attempt + 1)
                time.sleep(min(8, 2**attempt))
    raise InferenceError(f"inference failed: {last_error}")
