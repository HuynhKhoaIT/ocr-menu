"""OCR pipeline: image bytes → LLM → validated menu JSON.

Wraps the existing menu_ocr modules. LLM calls are sync (Anthropic/OpenAI
clients are sync); we run them in a thread so FastAPI's event loop stays
responsive when handling concurrent requests.
"""
import asyncio
import base64
from typing import Optional

from menu_ocr.clients_claude import call_claude_tool, get_client
from menu_ocr.clients_dispatch import get_provider
from menu_ocr.clients_openai import call_openai_tool, get_openai_client
from menu_ocr.image_utils import blur_score, normalize_image, pdf_all_pages_to_pngs
from menu_ocr.pricing import compute_cost
from menu_ocr.prompts import DEFAULT_PROMPT
from menu_ocr.validation import count_menu, validate_menu

from menu_api.settings import settings


def _build_pages(file_bytes: bytes, content_type: str):
    """Return [(png_bytes, blur_or_none)]."""
    if content_type == "application/pdf":
        page_pngs, _ = pdf_all_pages_to_pngs(file_bytes, settings.MAX_PDF_PAGES)
    else:
        page_pngs = [file_bytes]
    out = []
    for png in page_pngs:
        norm, _ = normalize_image(png, settings.MAX_IMAGE_SIDE)
        out.append((norm, blur_score(norm)))
    return out


def _init_client(model: str):
    """Return (client, error_or_none) for the model's provider."""
    if get_provider(model) == "openai":
        return get_openai_client(settings.OPENAI_API_KEY)
    return get_client(settings.ANTHROPIC_API_KEY)


def _call_llm(client, model: str, images_b64, max_tokens: int, page_context: str = ""):
    """page_context: optional prefix injected into the prompt when calling
    per-page so the model knows it sees a slice of a larger menu."""
    prompt = DEFAULT_PROMPT
    if page_context:
        prompt = f"{page_context}\n\n{DEFAULT_PROMPT}"
    if get_provider(model) == "openai":
        return call_openai_tool(client, model, prompt, images_b64, max_tokens)
    return call_claude_tool(
        client, model, prompt, images_b64, max_tokens, cache_prompt=True,
    )


# ============================================================
# Merge logic for parallel per-page extraction
# ============================================================
def merge_partial_menus(parts: list) -> dict:
    """Merge N partial menus (one per page) into a single menu.

    Rules:
      - food_conditions: dedup by name (case-insensitive). When the SAME name
        appears with DIFFERENT prices across pages → set canonical price to 0
        (conflict signal). When consistent → keep that price.
      - groups: dedup by name (case-insensitive). Same group name across pages
        → merge foods[] arrays (order preserved by page order).
      - Per-food beilage.food_datas[].price is NEVER touched (per-food snapshot).
    """
    # ----- food_conditions -----
    fc_seen: dict = {}  # key=name.lower() → {entry, prices_observed: set, conflict: bool}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for fc in part.get("food_conditions") or []:
            if not isinstance(fc, dict):
                continue
            name = (fc.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            price = fc.get("price") or 0
            if key not in fc_seen:
                fc_seen[key] = {
                    "entry": {
                        "name":         name,
                        "price":        price,
                        "plu":          fc.get("plu"),
                        "product_info": fc.get("product_info"),
                    },
                    "prices": {price},
                    "conflict": False,
                }
            else:
                fc_seen[key]["prices"].add(price)
                if len(fc_seen[key]["prices"]) > 1:
                    fc_seen[key]["conflict"] = True
                    fc_seen[key]["entry"]["price"] = 0   # conflict signal

    merged_fcs = [v["entry"] for v in fc_seen.values()]

    # ----- groups -----
    g_seen: dict = {}   # key=group_name.lower() → group dict (mutating foods)
    g_order: list = []  # preserve insertion order
    for part in parts:
        if not isinstance(part, dict):
            continue
        for g in part.get("groups") or []:
            if not isinstance(g, dict):
                continue
            name = (g.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in g_seen:
                # Same group across pages → extend foods
                g_seen[key]["foods"].extend(g.get("foods") or [])
                # Keep the longest description we've seen
                desc_new = g.get("description") or ""
                desc_cur = g_seen[key].get("description") or ""
                if len(desc_new) > len(desc_cur):
                    g_seen[key]["description"] = desc_new
            else:
                g_seen[key] = {
                    "name":        name,
                    "description": g.get("description"),
                    "foods":       list(g.get("foods") or []),
                }
                g_order.append(key)

    merged_groups = [g_seen[k] for k in g_order]

    return {"food_conditions": merged_fcs, "groups": merged_groups}


# ============================================================
# Parallel per-page extraction
# ============================================================
async def extract_per_page_parallel(
    pages_pngs: list,
    model: str,
    max_tokens: int,
):
    """Fan out one LLM call per PDF page, gather results in parallel.

    Returns dict:
      {
        "parts":       [raw_menu, ...]  # one per page that succeeded
        "page_results": [               # per-page metadata (always len = len(pages))
            {"page": 1, "ok": True,  "tool": "submit_menu",       "sec": 12.3, "usage": {...}, "stop_reason": "tool_use"},
            {"page": 2, "ok": False, "tool": "report_unreadable", "reason": "...", "suggestion": "..."},
            {"page": 3, "ok": False, "error": "API 429: ..."},
        ]
      }
    """
    client, err = _init_client(model)
    if err:
        raise RuntimeError(f"client init failed: {err}")

    total = len(pages_pngs)

    def _sync_call(idx: int, png: bytes):
        b64 = base64.b64encode(png).decode()
        ctx = (f"⚠️ MULTI-PAGE CONTEXT: This is page {idx + 1} of {total} from a larger "
               "PDF menu. Extract whatever you see on THIS page. Cross-page "
               "deduplication and group merging is handled server-side, so do not "
               "worry about being consistent with other pages — just be accurate "
               "for what you see here.")
        return _call_llm(client, model, [b64], max_tokens, page_context=ctx)

    # Run all pages in parallel — each in its own thread (sync SDK call)
    tasks = [asyncio.to_thread(_sync_call, i, png) for i, png in enumerate(pages_pngs)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    parts = []
    page_results = []
    for i, r in enumerate(results):
        page = i + 1
        if isinstance(r, Exception):
            page_results.append({"page": page, "ok": False, "error": f"{type(r).__name__}: {r}"})
            continue
        if r["error"]:
            page_results.append({"page": page, "ok": False, "error": r["error"],
                                 "sec": r["sec"]})
            continue
        if r["tool_name"] == "report_unreadable":
            ti = r["tool_input"] or {}
            page_results.append({
                "page": page, "ok": False, "tool": "report_unreadable",
                "reason": ti.get("reason"), "suggestion": ti.get("suggestion"),
                "sec": r["sec"], "usage": r["usage"], "stop_reason": r.get("stop_reason"),
            })
            continue
        if r["tool_name"] != "submit_menu":
            page_results.append({"page": page, "ok": False,
                                 "error": f"unexpected tool: {r['tool_name']}",
                                 "sec": r["sec"]})
            continue
        parts.append(r["tool_input"] or {"food_conditions": [], "groups": []})
        page_results.append({
            "page": page, "ok": True, "tool": "submit_menu",
            "sec": r["sec"], "usage": r["usage"], "stop_reason": r.get("stop_reason"),
        })

    return {"parts": parts, "page_results": page_results}


def _aggregate_usage(usages: list) -> dict:
    """Sum input/output/cache token counts across N successful calls."""
    out = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_create": 0}
    for u in usages:
        if not isinstance(u, dict):
            continue
        for k in out:
            out[k] += u.get(k, 0) or 0
    return out


async def extract_menu_from_image(
    file_bytes: bytes,
    content_type: str,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    skip_blur_check: bool = False,
    parallel: bool = False,
) -> dict:
    """Run the full extract pipeline. Returns a dict suitable for the OCRResponse model.

    When `parallel=True` AND there are >1 pages, run one LLM call per page
    concurrently and merge the results. Otherwise behave as single-call.

    Raises RuntimeError on init / API failure.
    """
    model = model or settings.DEFAULT_MODEL
    max_tokens = max_tokens or settings.DEFAULT_MAX_TOKENS

    pages = _build_pages(file_bytes, content_type)
    page_pngs = [png for png, _ in pages]
    images_b64 = [base64.b64encode(png).decode() for png in page_pngs]
    blur_scores = [bs for _, bs in pages if bs is not None and not skip_blur_check]
    worst_blur = min(blur_scores) if blur_scores else None

    # ------------------------------------------------------------
    # PARALLEL MODE — one call per page, merge afterwards
    # ------------------------------------------------------------
    if parallel and len(page_pngs) > 1:
        fan = await extract_per_page_parallel(page_pngs, model, max_tokens)
        parts = fan["parts"]
        page_results = fan["page_results"]

        # Total time = max of per-page (parallel) and total token cost = sum
        ok_results = [pr for pr in page_results if pr["ok"]]
        usages = [pr.get("usage") for pr in ok_results]
        agg_usage = _aggregate_usage(usages)
        total_sec = max((pr.get("sec", 0) for pr in page_results), default=0)
        any_truncated = any(
            (pr.get("stop_reason") in ("max_tokens", "length"))
            or ((pr.get("usage") or {}).get("output_tokens", 0) >= max_tokens)
            for pr in ok_results
        )

        base = {
            "blur_score": worst_blur, "usage": agg_usage, "sec": total_sec,
            "cost_usd": compute_cost(model, agg_usage),
            "stop_reason": "max_tokens" if any_truncated else "tool_use",
            "n_pages": len(images_b64), "model": model,
            "mode": "parallel-per-page",
            "page_results": page_results,
        }

        # No page succeeded at all
        if not parts:
            return {
                **base,
                "status": "unreadable",
                "reason": "no page returned a valid menu",
                "suggestion": "check page_results for per-page diagnostics",
            }

        # Merge + validate
        merged = merge_partial_menus(parts)
        valid_menu, errors = validate_menu(merged)
        counts = count_menu(valid_menu)
        return {
            **base,
            "status": "ok",
            "data": valid_menu,
            "raw": merged,
            "errors": errors,
            "counts": counts,
            "truncated": any_truncated,
            "max_tokens_used": max_tokens,
        }

    # ------------------------------------------------------------
    # SINGLE-CALL MODE (default — also for single-image input)
    # ------------------------------------------------------------
    client, err = _init_client(model)
    if err:
        raise RuntimeError(f"client init failed: {err}")

    r = await asyncio.to_thread(_call_llm, client, model, images_b64, max_tokens)
    if r["error"]:
        raise RuntimeError(r["error"])

    base = {
        "blur_score": worst_blur, "usage": r["usage"], "sec": r["sec"],
        "cost_usd": compute_cost(model, r["usage"]),
        "stop_reason": r.get("stop_reason"),
        "n_pages": len(images_b64), "model": model,
        "mode": "single-call",
    }

    if r["tool_name"] == "report_unreadable":
        ti = r["tool_input"] or {}
        return {**base, "status": "unreadable",
                "reason": ti.get("reason"), "suggestion": ti.get("suggestion")}

    if r["tool_name"] != "submit_menu":
        raise RuntimeError(f"unexpected tool: {r['tool_name']!r}")

    raw_menu = r["tool_input"] or {"food_conditions": [], "groups": []}
    valid_menu, errors = validate_menu(raw_menu)
    counts = count_menu(valid_menu)

    out_tokens = (r["usage"] or {}).get("output_tokens", 0)
    truncated = (
        r.get("stop_reason") in ("max_tokens", "length")
        or out_tokens >= max_tokens
    )

    return {
        **base, "status": "ok", "data": valid_menu, "raw": raw_menu,
        "errors": errors, "counts": counts,
        "truncated": truncated, "max_tokens_used": max_tokens,
    }
