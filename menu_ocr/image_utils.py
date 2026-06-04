"""PDF → PNG / text extraction, image normalization, blur estimation, auto-crop, CLAHE."""
import io
from typing import List, Optional, Tuple

from menu_ocr.config import (
    AUTO_CROP_PADDING,
    AUTO_CROP_SAFETY_FRAC,
    CLAHE_CLIP_LIMIT,
    CLAHE_TILE_GRID,
    MAX_PDF_PAGES,
    PDF_TEXT_THUMB_DPI,
    PDF_TEXT_CHARS_PER_PAGE_THRESHOLD,
    RESIZE_MAX_SIDE,
    SHARPEN_BELOW_BLUR_SCORE,
)


# ============================================================
# PDF — text vs image detection
# ============================================================
def pdf_classify(file_bytes: bytes) -> dict:
    """Decide whether a PDF carries an embedded text layer ('text-PDF') or is a
    scan/photo ('image-PDF'). Returns:
        {
          "kind":            "text" | "image",
          "page_count":      int,
          "chars_per_page":  float,
          "total_chars":     int,
        }
    """
    import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    page_count = doc.page_count
    if page_count == 0:
        return {"kind": "image", "page_count": 0, "chars_per_page": 0.0, "total_chars": 0}
    total = 0
    for p in doc:
        total += len(p.get_text("text").strip())
    cpp = total / page_count
    return {
        "kind": "text" if cpp >= PDF_TEXT_CHARS_PER_PAGE_THRESHOLD else "image",
        "page_count": page_count,
        "chars_per_page": cpp,
        "total_chars": total,
    }


def pdf_extract_text_per_page(file_bytes: bytes, max_pages: int = MAX_PDF_PAGES) -> List[str]:
    """Extract text per page from a text-PDF, preserving reading order.

    Uses get_text("blocks") and sorts top-to-bottom, left-to-right so that
    item name and price end up on the same logical line — critical for
    downstream LLM to associate them.
    """
    import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    out: List[str] = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        # blocks: [(x0, y0, x1, y1, "text", block_no, block_type), ...]
        blocks = page.get_text("blocks")
        # Sort by y then x to reconstruct reading order.
        blocks = sorted(
            [b for b in blocks if b[6] == 0 and (b[4] or "").strip()],
            key=lambda b: (round(b[1] / 8), b[0]),
        )
        out.append("\n".join((b[4] or "").strip() for b in blocks))
    return out


def pdf_render_thumbnails(file_bytes: bytes, max_pages: int = MAX_PDF_PAGES,
                          dpi: int = PDF_TEXT_THUMB_DPI) -> List[bytes]:
    """Render low-DPI PNG thumbnails for layout verification — cheap on tokens.
    Used together with extracted text so the model can resolve ambiguity
    (which price belongs to which dish) without paying for full-resolution OCR.
    """
    import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    out: List[bytes] = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        out.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    return out


def pdf_all_pages_to_pngs(file_bytes, max_pages=MAX_PDF_PAGES):
    """Render ALL pages of an image-PDF into full-res PNGs (DPI=200).
    Returns (list_of_png_bytes, original_page_count)."""
    import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        pix = page.get_pixmap(dpi=200)
        pages.append(pix.tobytes("png"))
    return pages, doc.page_count


# ============================================================
# Image preprocessing
# ============================================================
def normalize_image(raw_bytes, max_side=RESIZE_MAX_SIDE):
    """Convert to RGB and downscale if the longest side exceeds max_side.
    Default 1568 matches Anthropic's tile boundary (~1.15MP) for cheapest
    full-quality vision input."""
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue(), img.size


def auto_crop(png_bytes: bytes, pad: int = AUTO_CROP_PADDING) -> bytes:
    """Crop white/empty margins around the menu content. Safe-by-default —
    if the detected content bbox covers ≥ AUTO_CROP_SAFETY_FRAC of the image,
    assume the menu already fills the frame and return the original.
    Returns original on any error (cv2 missing, weird input, etc.)."""
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return png_bytes
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # Invert + threshold so "content" becomes non-zero.
        _, thr = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)
        coords = cv2.findNonZero(thr)
        if coords is None:
            return png_bytes
        x, y, w, h = cv2.boundingRect(coords)
        H, W = gray.shape
        if w * h >= AUTO_CROP_SAFETY_FRAC * H * W:
            return png_bytes  # already fills the frame — nothing to crop
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(W, x + w + pad)
        y1 = min(H, y + h + pad)
        cropped = img[y0:y1, x0:x1]
        ok, buf = cv2.imencode(".png", cropped)
        return buf.tobytes() if ok else png_bytes
    except Exception:
        return png_bytes


def enhance_contrast(png_bytes: bytes) -> bytes:
    """CLAHE on L-channel — improves backlit/uneven-light menu photos.
    Color is preserved (only luminance is equalized).
    Returns original on any error."""
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return png_bytes
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT,
                                tileGridSize=CLAHE_TILE_GRID)
        l = clahe.apply(l)
        merged = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
        ok, buf = cv2.imencode(".png", merged)
        return buf.tobytes() if ok else png_bytes
    except Exception:
        return png_bytes


def sharpen_if_blurry(png_bytes: bytes,
                      threshold: float = SHARPEN_BELOW_BLUR_SCORE) -> bytes:
    """Apply unsharp mask only when measured sharpness is below threshold.
    Cheap when not needed (one Laplacian call)."""
    try:
        import cv2
        import numpy as np
        bs = blur_score(png_bytes)
        if bs is None or bs >= threshold:
            return png_bytes
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return png_bytes
        blur = cv2.GaussianBlur(img, (0, 0), 3)
        sharpened = cv2.addWeighted(img, 1.5, blur, -0.5, 0)
        ok, buf = cv2.imencode(".png", sharpened)
        return buf.tobytes() if ok else png_bytes
    except Exception:
        return png_bytes


def preprocess_pipeline(png_bytes: bytes, *, do_crop: bool = True,
                        do_clahe: bool = True, do_sharpen: bool = True,
                        max_side: int = RESIZE_MAX_SIDE) -> Tuple[bytes, Tuple[int, int]]:
    """Full preprocessing in one call: crop → CLAHE → sharpen → normalize.
    Each step degrades gracefully (returns input unchanged on failure)."""
    out = png_bytes
    if do_crop:
        out = auto_crop(out)
    if do_clahe:
        out = enhance_contrast(out)
    if do_sharpen:
        out = sharpen_if_blurry(out)
    out, size = normalize_image(out, max_side=max_side)
    return out, size


# ============================================================
# Sharpness measurement
# ============================================================
def blur_score(png_bytes) -> Optional[float]:
    """Return Laplacian variance (higher = sharper). None if cv2 is unavailable."""
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return None
