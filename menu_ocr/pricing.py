"""Token pricing table + cost computation.

Prices are $/1M tokens. Update when providers change rates.
OpenAI cache_write = 0 because OpenAI caches automatically without a separate
write charge; we keep the key for schema parity with Anthropic.
"""
from typing import Optional


PRICING_PER_MTOK = {
    # ===== Anthropic =====
    "claude-opus-4-7":             {"in": 15.0, "out": 75.0, "cache_read": 1.50,  "cache_write": 18.75},
    "claude-sonnet-4-6":           {"in":  3.0, "out": 15.0, "cache_read": 0.30,  "cache_write":  3.75},
    "claude-haiku-4-5-20251001":   {"in":  1.0, "out":  5.0, "cache_read": 0.10,  "cache_write":  1.25},
    "claude-3-7-sonnet-20250219":  {"in":  3.0, "out": 15.0, "cache_read": 0.30,  "cache_write":  3.75},
    "claude-3-5-sonnet-20241022":  {"in":  3.0, "out": 15.0, "cache_read": 0.30,  "cache_write":  3.75},
    "claude-3-5-haiku-20241022":   {"in":  0.8, "out":  4.0, "cache_read": 0.08,  "cache_write":  1.00},
    # ===== OpenAI =====
    "gpt-4o":                      {"in":  2.50, "out": 10.00, "cache_read": 1.25,   "cache_write": 0.0},
    "gpt-4o-mini":                 {"in":  0.15, "out":  0.60, "cache_read": 0.075,  "cache_write": 0.0},
    "gpt-4.1":                     {"in":  2.00, "out":  8.00, "cache_read": 0.50,   "cache_write": 0.0},
    "gpt-4.1-mini":                {"in":  0.40, "out":  1.60, "cache_read": 0.10,   "cache_write": 0.0},
    "gpt-4.1-nano":                {"in":  0.10, "out":  0.40, "cache_read": 0.025,  "cache_write": 0.0},
    "gpt-5":                       {"in":  1.25, "out": 10.00, "cache_read": 0.125,  "cache_write": 0.0},
    "gpt-5-mini":                  {"in":  0.25, "out":  2.00, "cache_read": 0.025,  "cache_write": 0.0},
    # gpt-5.5 / 5.5-pro: placeholder pricing — verify against OpenAI billing once published.
    "gpt-5.5":                     {"in":  1.50, "out": 12.00, "cache_read": 0.15,   "cache_write": 0.0},
    "gpt-5.5-pro":                 {"in":  5.00, "out": 40.00, "cache_read": 0.50,   "cache_write": 0.0},
    "o3":                          {"in": 10.00, "out": 40.00, "cache_read": 2.50,   "cache_write": 0.0},
    "o4-mini":                     {"in":  1.10, "out":  4.40, "cache_read": 0.275,  "cache_write": 0.0},
}


def compute_cost(model: str, usage: dict) -> Optional[float]:
    """Compute dollar cost from usage. Returns None if pricing/usage missing.

    Note: `input_tokens` in Anthropic responses already excludes cache_read; the
    OpenAI wrapper does the same subtraction so the formula is universal.
    """
    if not usage or model not in PRICING_PER_MTOK:
        return None
    p = PRICING_PER_MTOK[model]
    base_in = usage["input_tokens"]
    cache_r = usage["cache_read"]
    cache_w = usage["cache_create"]
    out_t   = usage["output_tokens"]
    cost = (base_in * p["in"] + out_t * p["out"]
            + cache_r * p["cache_read"] + cache_w * p["cache_write"]) / 1_000_000
    return cost
