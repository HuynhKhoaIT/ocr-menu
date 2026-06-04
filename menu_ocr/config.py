"""Shared constants: model fallback lists, type/kind enums, blur thresholds.

Enums mirror the CMS (hq-qrcode-admin) numeric IDs so the LLM output is
consumable by the create APIs with zero conversion.
"""

# ===== Anthropic =====
CLAUDE_MODELS_FALLBACK = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]

# ===== OpenAI =====
OPENAI_MODELS_FALLBACK = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
]


def is_cheap_model(model_id: str) -> bool:
    """Heuristic: which model is cheap/fast enough for the OCR step."""
    m = model_id.lower()
    return "haiku" in m or "sonnet" in m or "mini" in m or "nano" in m


# ===== CMS enums =====
# GoodsTypes  = { DRINK: 1, FOOD: 2 }
# GoodsKinds  = { COMMON: 1, CHOOSE: 5 }
TYPE_DRINK,  TYPE_FOOD   = 1, 2
KIND_COMMON, KIND_CHOOSE = 1, 5
FOOD_TYPES  = [TYPE_DRINK, TYPE_FOOD]
FOOD_KINDS  = [KIND_COMMON, KIND_CHOOSE]
TYPE_LABELS = {TYPE_DRINK: "DRINK", TYPE_FOOD: "FOOD"}
KIND_LABELS = {KIND_COMMON: "COMMON", KIND_CHOOSE: "CHOOSE"}


# ===== Limits / thresholds =====
MAX_PDF_PAGES = 10  # avoid blast cost for thick PDFs

# Laplacian variance thresholds (measured after normalize to 1568px):
#   > 500   : sharp
#   200-500 : soft but readable
#   100-200 : likely misreads small digits (prices!)
#   < 100   : almost certain to hallucinate
BLUR_WARN_THRESHOLD  = 500.0   # below → yellow warning + user must confirm
BLUR_BLOCK_THRESHOLD = 100.0   # below → hard block

# ===== Image preprocessing =====
# 1568px matches Anthropic's 1.15MP vision-tile boundary — biggest size that
# still fits in the cheapest tier. Going larger costs more without accuracy gain.
RESIZE_MAX_SIDE = 1568

# Auto-crop: skip cropping if detected content already fills ≥ this fraction of
# the frame (menu chụp full khung không cần crop).
AUTO_CROP_SAFETY_FRAC = 0.95
AUTO_CROP_PADDING     = 24

# CLAHE — local contrast equalization for ảnh ngược sáng / nền xám.
CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILE_GRID  = (8, 8)

# Only sharpen if blur_score below this (sharpening sharp images adds noise).
SHARPEN_BELOW_BLUR_SCORE = 800.0

# ===== PDF text-vs-image detection =====
# A text-PDF averages thousands of chars/page; a scan averages 0.
# 100 chars/page comfortably separates them and tolerates header-only pages.
PDF_TEXT_CHARS_PER_PAGE_THRESHOLD = 100

# Low-DPI thumbnail for text-PDFs — model only needs to verify layout, not OCR.
# 100 DPI → ~400×550 PNG → ~250 vision tokens vs ~1500 for full DPI=200.
PDF_TEXT_THUMB_DPI = 100
