"""Anthropic (Claude) client + tool/text calls.

Returns a unified dict shape so the dispatcher can swap providers transparently.
"""
import os
import time
from typing import List

from menu_ocr.config import CLAUDE_MODELS_FALLBACK

try:
    from anthropic import Anthropic, APIError, APIStatusError
except ImportError:
    Anthropic = None
    APIError = APIStatusError = Exception


def get_client(api_key: str):
    """Init Anthropic client with retry + timeout. Returns (client, error)."""
    if Anthropic is None:
        return None, "Anthropic SDK not installed: pip install anthropic"
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None, "ANTHROPIC_API_KEY missing."
    try:
        # max_retries: SDK retries 429/5xx with exponential backoff.
        # timeout: 300s for Opus 4.7 reasoning + multi-page PDFs.
        return Anthropic(api_key=key, max_retries=3, timeout=300.0), None
    except Exception as e:
        return None, f"Init client failed: {type(e).__name__}"


def list_available_models(client) -> List[str]:
    """Call /v1/models to list models this account can use."""
    try:
        result = client.models.list(limit=50)
        ids = [m.id for m in result.data if m.id.startswith("claude-")]
        ids.sort(reverse=True)
        return ids or CLAUDE_MODELS_FALLBACK
    except Exception:
        return CLAUDE_MODELS_FALLBACK


def _img_block(b64: str) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }


# Claude models that REJECT temperature overrides with 400:
#   'temperature is deprecated for this model'.
# Opus 4.7 is reasoning-tier — same restriction as OpenAI gpt-5/o-series.
# Add new model ids here as Anthropic rolls them out.
_CLAUDE_NO_TEMPERATURE = ("opus-4-7",)


def _sampling_kwargs(model: str) -> dict:
    """Return temperature kwargs only when the model accepts them."""
    m = model.lower()
    if any(tag in m for tag in _CLAUDE_NO_TEMPERATURE):
        return {}
    return {"temperature": 0}


def _err_result(t0, msg):
    return {
        "tool_name": None, "tool_input": None, "raw_text": "",
        "usage": None, "sec": time.time() - t0, "error": msg,
        "stop_reason": None,
    }


def call_claude_tool(client, model, prompt, image_b64_list=None,
                     max_tokens=4096, cache_prompt=True, *, tools, hint=None):
    """Force-call a tool. Returns:
        {tool_name, tool_input, raw_text, usage, sec, error}
    `tools` MUST be the [submit_*, report_unreadable] pair for this step.
    `hint` is a short user-message nudge telling the model which tool to call.
    """
    sys_blocks = [{
        "type": "text",
        "text": prompt,
        **({"cache_control": {"type": "ephemeral"}} if cache_prompt else {}),
    }]

    user_content = []
    if image_b64_list:
        for i, b64 in enumerate(image_b64_list):
            if len(image_b64_list) > 1:
                user_content.append({"type": "text", "text": f"--- Page {i+1}/{len(image_b64_list)} ---"})
            user_content.append(_img_block(b64))
    user_content.append({
        "type": "text",
        "text": hint or (
            "Call the appropriate submit tool if the image is clearly readable, "
            "or `report_unreadable` if it is not. DO NOT return plain text."
        ),
    })

    t0 = time.time()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=sys_blocks,
            messages=[{"role": "user", "content": user_content}],
            tools=tools,
            tool_choice={"type": "any"},
            **_sampling_kwargs(model),   # temperature=0 when the model accepts it
        )
    except APIStatusError as e:
        msg = f"API {e.status_code}: {getattr(e, 'message', str(e))[:200]}"
        if e.status_code == 404:
            msg += "  →  Model not found / account not granted. Click '🔄 Reload models' in the sidebar."
        return _err_result(t0, msg)
    except APIError as e:
        return _err_result(t0, f"API error: {type(e).__name__}")
    except Exception as e:
        return _err_result(t0, f"{type(e).__name__}: {str(e)[:200]}")

    elapsed = time.time() - t0

    tool_name, tool_input, raw_text = None, None, ""
    for block in resp.content:
        btype = getattr(block, "type", "")
        if btype == "tool_use":
            tool_name = block.name
            tool_input = block.input
        elif btype == "text":
            raw_text += block.text

    usage = {
        "input_tokens":  getattr(resp.usage, "input_tokens", 0),
        "output_tokens": getattr(resp.usage, "output_tokens", 0),
        "cache_read":    getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        "cache_create":  getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
    }
    return {
        "tool_name": tool_name, "tool_input": tool_input, "raw_text": raw_text,
        "usage": usage, "sec": elapsed, "error": None,
        "stop_reason": getattr(resp, "stop_reason", None),
    }


def call_claude_text(client, model, prompt, image_b64_list=None,
                     max_tokens=4096, cache_prompt=True):
    """Plain-text call (for the OCR step). Returns (text, sec, error, usage)."""
    sys_blocks = [{
        "type": "text", "text": prompt,
        **({"cache_control": {"type": "ephemeral"}} if cache_prompt else {}),
    }]
    user_content = []
    if image_b64_list:
        for i, b64 in enumerate(image_b64_list):
            if len(image_b64_list) > 1:
                user_content.append({"type": "text", "text": f"--- Page {i+1}/{len(image_b64_list)} ---"})
            user_content.append(_img_block(b64))
    user_content.append({"type": "text", "text": "Follow the instructions in the system prompt."})

    t0 = time.time()
    try:
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=sys_blocks,
            messages=[{"role": "user", "content": user_content}],
            **_sampling_kwargs(model),
        )
    except APIStatusError as e:
        msg = f"API {e.status_code}"
        if e.status_code == 404:
            msg += " — model not found / account not granted. Click '🔄 Reload models'."
        return "", time.time() - t0, msg, None
    except Exception as e:
        return "", time.time() - t0, f"{type(e).__name__}: {str(e)[:200]}", None

    parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    usage = {
        "input_tokens":  getattr(resp.usage, "input_tokens", 0),
        "output_tokens": getattr(resp.usage, "output_tokens", 0),
        "cache_read":    getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        "cache_create":  getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
    }
    return "".join(parts), time.time() - t0, None, usage
