"""OpenAI client + tool/text calls.

Result shape mirrors clients_claude.py so the dispatcher is provider-agnostic.
"""
import json
import os
import time
from typing import List

from menu_ocr.config import OPENAI_MODELS_FALLBACK

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


def get_openai_client(api_key: str):
    """Init OpenAI client. Returns (client, error).

    Reasoning models (gpt-5.x, o-series) take MUCH longer than chat models —
    typical 60–300s for menu-OCR-sized inputs. Default timeout=300s + only 1
    retry (so worst case = 10 min instead of 8+) so the user is not left
    staring at a spinner forever.
    """
    if OpenAI is None:
        return None, "OpenAI SDK not installed: pip install openai"
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return None, "OPENAI_API_KEY missing."
    try:
        return OpenAI(api_key=key, max_retries=1, timeout=300.0), None
    except Exception as e:
        return None, f"Init failed: {type(e).__name__}"


def list_available_openai_models(client) -> List[str]:
    """Call /v1/models, filter to vision-capable chat models."""
    try:
        result = client.models.list()
        ids = [m.id for m in result.data]
        # Keep gpt-4*, gpt-5*, o1/o3/o4*. Drop audio/tts/whisper/embed/instruct.
        kept = [m for m in ids
                if m.startswith(("gpt-4", "gpt-5", "o1", "o3", "o4"))
                and not any(x in m for x in ["audio", "tts", "whisper",
                                              "embed", "instruct", "realtime",
                                              "transcribe", "search", "moderation"])]
        kept.sort(reverse=True)
        return kept or OPENAI_MODELS_FALLBACK
    except Exception:
        return OPENAI_MODELS_FALLBACK


def _openai_user_content(image_b64_list, final_text):
    """Build OpenAI vision user content (base64 data URI)."""
    content = []
    for i, b64 in enumerate(image_b64_list or []):
        if len(image_b64_list) > 1:
            content.append({"type": "text", "text": f"--- Page {i+1}/{len(image_b64_list)} ---"})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    content.append({"type": "text", "text": final_text})
    return content


def _sampling_kwargs(model: str) -> dict:
    """Return temperature/seed kwargs only when the model accepts them.

    GPT-5.x and o-series (o1/o3/o4) are 'reasoning' models — the API rejects
    any temperature override with 400: 'Only the default (1) value is
    supported'. For those we omit both temperature and seed.
    Older gpt-4.x accept temperature=0 + seed for deterministic output.
    """
    m = model.lower()
    if m.startswith(("gpt-5", "o1", "o3", "o4")):
        return {}
    return {"temperature": 0, "seed": 42}


def _openai_usage(resp_usage) -> dict:
    """Convert OpenAI usage → shape matching Anthropic.
    OpenAI prompt_tokens INCLUDES cached; Anthropic input_tokens DOES NOT.
    Subtract cached so compute_cost can use one formula."""
    cached = 0
    details = getattr(resp_usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    prompt_total = getattr(resp_usage, "prompt_tokens", 0)
    return {
        "input_tokens":  max(0, prompt_total - cached),
        "output_tokens": getattr(resp_usage, "completion_tokens", 0),
        "cache_read":    cached,
        "cache_create":  0,
    }


def _err_result(t0, msg):
    return {
        "tool_name": None, "tool_input": None, "raw_text": "",
        "usage": None, "sec": time.time() - t0, "error": msg,
        "stop_reason": None,
    }


def call_openai_tool(client, model, prompt, image_b64_list=None, max_tokens=4096,
                     *, tools, hint=None):
    """Force-call a tool via chat completions. Same return shape as Claude.
    `tools` MUST be the OpenAI-wrapped [submit_*, report_unreadable] pair.
    """
    user_content = _openai_user_content(
        image_b64_list,
        hint or (
            "Call the appropriate submit function if the image is clearly readable, "
            "or `report_unreadable` otherwise. DO NOT return plain text."
        ),
    )
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user",   "content": user_content},
            ],
            tools=tools,
            tool_choice="required",
            **_sampling_kwargs(model),
        )
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        if "404" in msg or "not_found" in msg.lower():
            msg += "  →  Model not available. Click refresh in the sidebar."
        return _err_result(t0, msg)

    elapsed = time.time() - t0
    msg = resp.choices[0].message
    raw_text = msg.content or ""
    tool_name, tool_input = None, None
    if msg.tool_calls:
        tc = msg.tool_calls[0]
        tool_name = tc.function.name
        try:
            tool_input = json.loads(tc.function.arguments)
        except Exception:
            tool_input = None
    return {
        "tool_name": tool_name, "tool_input": tool_input, "raw_text": raw_text,
        "usage": _openai_usage(resp.usage), "sec": elapsed, "error": None,
        "stop_reason": getattr(resp.choices[0], "finish_reason", None),
    }


def call_openai_text(client, model, prompt, image_b64_list=None, max_tokens=4096):
    """Plain-text call for the OCR step."""
    user_content = _openai_user_content(image_b64_list, "Follow the instructions in the system prompt.")
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model, max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user",   "content": user_content},
            ],
            **_sampling_kwargs(model),
        )
    except Exception as e:
        return "", time.time() - t0, f"{type(e).__name__}: {str(e)[:150]}", None
    return (resp.choices[0].message.content or ""), time.time() - t0, None, _openai_usage(resp.usage)
