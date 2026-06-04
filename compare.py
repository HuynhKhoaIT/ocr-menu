"""Run Google Vision and OpenAI OCR side-by-side on the same menu image.

Usage:
    python compare.py <image-path>
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from menu_scan import scan_with_google_vision
from menu_scan_openai import scan_with_openai

load_dotenv()

COL_WIDTH = 42


def _safe_call(fn, image_path):
    try:
        return fn(image_path)
    except Exception as e:
        return {"error": str(e)}


def _pad(s: str, w: int) -> str:
    s = str(s)
    return (s[: w - 1] + "…") if len(s) >= w else s + " " * (w - len(s))


def _summary_row(label: str, r: dict) -> str:
    count = f"{r.get('itemCount', '—')} items".ljust(12)
    ms = f"{r.get('elapsedMs', '—')}ms".ljust(8)
    extra = ""
    if r.get("usage"):
        u = r["usage"]
        extra = f"  tokens in/out: {u['prompt_tokens']}/{u['completion_tokens']}"
    if r.get("error"):
        extra += f"  ERROR: {r['error']}"
    return f"{label:<14}: {count}  |  {ms}{extra}"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python compare.py <image-path>", file=sys.stderr)
        sys.exit(1)
    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"File not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        gg_future = pool.submit(_safe_call, scan_with_google_vision, image_path)
        oa_future = pool.submit(_safe_call, scan_with_openai, image_path)
        gg = gg_future.result()
        oa = oa_future.result()

    print("\n=== SUMMARY ===")
    print(_summary_row("Google Vision", gg))
    print(_summary_row("OpenAI", oa))

    print("\n=== ITEMS (side by side) ===")
    print(_pad("GOOGLE VISION", COL_WIDTH) + " | OPENAI")
    print("-" * COL_WIDTH + "-+-" + "-" * COL_WIDTH)
    gg_items = gg.get("items") or []
    oa_items = oa.get("items") or []
    def _fmt(item: dict) -> str:
        price = item.get("price")
        if price is None:
            seen = item.get("priceText") or "???"
            return f"{item['name']} — null (saw: {seen})"
        return f"{item['name']} — {price}"

    for i in range(max(len(gg_items), len(oa_items))):
        left = _fmt(gg_items[i]) if i < len(gg_items) else ""
        right = _fmt(oa_items[i]) if i < len(oa_items) else ""
        print(_pad(left, COL_WIDTH) + " | " + right)

    out_dir = Path(image_path).resolve().parent
    out_file = out_dir / f"{Path(image_path).stem}.compare.json"
    out_file.write_text(
        json.dumps({"googleVision": gg, "openai": oa}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nFull JSON saved to: {out_file}")


if __name__ == "__main__":
    main()
