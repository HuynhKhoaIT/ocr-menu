"""Google Vision OCR for menus.

Usage:
    python menu_scan.py <image-path>
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import vision

load_dotenv()

# VND with thousand separators + optional currency suffix: 50.000 / 50,000 / 50.000đ
_PAT_VND = re.compile(
    r"^(.*?)[\s\-:.]*(\d{1,3}(?:[.,]\d{3})+)\s*(?:đ|đồng|vnđ|vnd|d)?\s*$",
    re.IGNORECASE,
)
# Shorthand: 50k / 50K  -> 50000
_PAT_K = re.compile(r"^(.*?)[\s\-:.]*(\d{1,4})\s*k\s*$", re.IGNORECASE)
# Currency-symbol prefix: $5, $5.00, €4,50
_PAT_SYMBOL = re.compile(r"^(.*?)[\s\-:.]*[$€£]\s*(\d+(?:[.,]\d{1,2})?)\s*$")
# Decimal + optional suffix: 5.00 / 4,50 USD
_PAT_DECIMAL = re.compile(
    r"^(.*?)[\s\-:.]*(\d+[.,]\d{2})\s*(?:usd|eur|gbp)?\s*$", re.IGNORECASE
)

_PATTERNS = [
    ("vnd", _PAT_VND),
    ("k", _PAT_K),
    ("symbol", _PAT_SYMBOL),
    ("decimal", _PAT_DECIMAL),
]


def parse_line(line: str) -> dict | None:
    for kind, pat in _PATTERNS:
        m = pat.match(line)
        if not m:
            continue
        name = m.group(1).strip().rstrip(" -:.").strip()
        if len(name) < 2:
            continue
        raw_price = m.group(2)
        price = _normalize_price(raw_price, kind)
        if price is None:
            continue
        return {"name": name, "price": price, "priceText": raw_price, "raw": line}
    return None


def _normalize_price(raw: str, kind: str) -> float | int | None:
    if kind == "k":
        return int(raw) * 1000
    if kind == "vnd":
        return int(raw.replace(".", "").replace(",", ""))
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def scan_with_google_vision(image_path: str) -> dict:
    client = vision.ImageAnnotatorClient()
    with open(image_path, "rb") as f:
        content = f.read()

    started = time.time()
    response = client.document_text_detection(
        image=vision.Image(content=content),
        image_context=vision.ImageContext(language_hints=["vi", "en"]),
    )
    elapsed_ms = int((time.time() - started) * 1000)

    if response.error.message:
        raise RuntimeError(response.error.message)

    full_text = response.full_text_annotation.text if response.full_text_annotation else ""
    lines = [ln.strip() for ln in full_text.split("\n") if ln.strip()]

    items = [parsed for ln in lines if (parsed := parse_line(ln))]

    return {
        "image": str(Path(image_path).resolve()),
        "elapsedMs": elapsed_ms,
        "itemCount": len(items),
        "items": items,
        "rawLines": lines,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python menu_scan.py <image-path>", file=sys.stderr)
        sys.exit(1)
    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"File not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    result = scan_with_google_vision(image_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
