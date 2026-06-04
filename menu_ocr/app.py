"""Streamlit UI — Menu → JSON POS · FC-first 2-call pipeline.

Architecture:
  Call 1 (cheap model)  → submit_food_conditions  → whitelist of modifiers
  Call 2 (strong model) → submit_menu_groups      → groups[] referencing whitelist
  Validate + merge      → final {food_conditions, groups}

Entry point: `main()` is invoked by both `streamlit run menu_ocr/app.py` and the
top-level shim `menu_compare_claude.py`.
"""
import os as _os
import sys as _sys
_PKG_PARENT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PKG_PARENT not in _sys.path:
    _sys.path.insert(0, _PKG_PARENT)

import base64
import hmac
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Tuple

import streamlit as st

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from menu_ocr.clients_claude import get_client, list_available_models
from menu_ocr.clients_dispatch import (
    call_fc_scan,
    call_menu_build,
    get_provider,
)
from menu_ocr.clients_openai import get_openai_client, list_available_openai_models
from menu_ocr.config import (
    BLUR_BLOCK_THRESHOLD,
    BLUR_WARN_THRESHOLD,
    CLAUDE_MODELS_FALLBACK,
    KIND_CHOOSE,
    KIND_LABELS,
    OPENAI_MODELS_FALLBACK,
    RESIZE_MAX_SIDE,
    TYPE_LABELS,
    is_cheap_model,
)
from menu_ocr.image_utils import (
    blur_score as _blur_score_raw,
    pdf_all_pages_to_pngs as _pdf_all_pages_to_pngs_raw,
    pdf_classify as _pdf_classify_raw,
    pdf_extract_text_per_page as _pdf_extract_text_raw,
    pdf_render_thumbnails as _pdf_render_thumbnails_raw,
    preprocess_pipeline as _preprocess_pipeline_raw,
)


# ----- cached wrappers (Streamlit reruns the script on every widget tick) -----
@st.cache_data(show_spinner=False)
def preprocess_pipeline(raw_bytes: bytes, max_side: int,
                        do_crop: bool, do_clahe: bool, do_sharpen: bool):
    return _preprocess_pipeline_raw(
        raw_bytes, max_side=max_side,
        do_crop=do_crop, do_clahe=do_clahe, do_sharpen=do_sharpen,
    )


@st.cache_data(show_spinner=False)
def blur_score(png_bytes: bytes):
    return _blur_score_raw(png_bytes)


@st.cache_data(show_spinner=False)
def pdf_classify(file_bytes: bytes):
    return _pdf_classify_raw(file_bytes)


@st.cache_data(show_spinner=False)
def pdf_extract_text_per_page(file_bytes: bytes, max_pages: int):
    return _pdf_extract_text_raw(file_bytes, max_pages)


@st.cache_data(show_spinner=False)
def pdf_render_thumbnails(file_bytes: bytes, max_pages: int, dpi: int):
    return _pdf_render_thumbnails_raw(file_bytes, max_pages, dpi)


@st.cache_data(show_spinner=False)
def pdf_all_pages_to_pngs(file_bytes: bytes, max_pages: int):
    return _pdf_all_pages_to_pngs_raw(file_bytes, max_pages)


@st.cache_data(show_spinner=False)
def encode_b64(png_bytes: bytes) -> str:
    return base64.b64encode(png_bytes).decode()


from menu_ocr.pricing import compute_cost
from menu_ocr.prompts import FC_SCAN_PROMPT, MENU_BUILD_PROMPT, TEXT_PDF_PROMPT_PREFIX
from menu_ocr.scoring import score_against_truth
from menu_ocr.validation import (
    count_menu,
    merge_fc_and_groups,
    merge_groups_payloads,
    validate_menu,
)


# Concurrency cap for per-page Call-2 fan-out. Higher = faster on big PDFs but
# risks rate-limit (Anthropic tier 1: 50 RPM, OpenAI varies by tier).
# 4 is safe for typical accounts.
MAX_PARALLEL_MENU_CALLS = 4


def _is_reasoning_model(model_id: str) -> bool:
    m = model_id.lower()
    return (m.startswith(("gpt-5", "o1", "o3", "o4"))
            or "opus-4-7" in m
            or "opus-4-8" in m)


# ============================================================
#                       AUTH GATE
# ============================================================
def _auth_gate():
    app_pw = os.environ.get("APP_PASSWORD", "")
    if not app_pw:
        return
    if st.session_state.get("authed"):
        return
    st.title("🔒 Đăng nhập")
    st.caption("App này được bảo vệ. Nhập password để dùng.")
    pw = st.text_input("Password", type="password")
    if st.button("Vào"):
        if hmac.compare_digest(pw, app_pw):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Sai password.")
    st.stop()


def _compute_run_cost(h: dict) -> float:
    """Cost = Call-1 (FC) + Call-2 (menu)."""
    c_fc   = compute_cost(h["model_fc"],   h.get("fc_usage")   or {}) or 0 if h.get("model_fc") else 0
    c_menu = compute_cost(h["model_menu"], h.get("menu_usage") or {}) or 0 if h.get("model_menu") else 0
    return c_fc + c_menu


# ============================================================
#                       UPLOAD PIPELINE
# ============================================================
def _process_uploaded_file(uploaded, max_side: int, max_pdf_pages: int, *,
                           do_crop: bool, do_clahe: bool, do_sharpen: bool,
                           blur_check_on: bool) -> Tuple[List[str], List[float], str, List[str]]:
    """Read upload → render PDF pages (text or image path) → preprocess each
    page → render previews.

    Returns: (images_b64, blur_scores, source_kind, extracted_text_per_page)
        source_kind ∈ {"image", "pdf_image", "pdf_text"}
        extracted_text_per_page is non-empty only when source_kind == "pdf_text"
    """
    raw = uploaded.getvalue()
    images_b64: List[str] = []
    blur_scores: List[float] = []
    text_per_page: List[str] = []

    if uploaded.type == "application/pdf":
        info = pdf_classify(raw)
        st.caption(
            f"📄 PDF · {info['page_count']} trang · "
            f"{info['chars_per_page']:.0f} ký tự/trang → **{info['kind']}-PDF**"
        )
        if info["kind"] == "text":
            text_per_page = pdf_extract_text_per_page(raw, max_pdf_pages)
            page_pngs = pdf_render_thumbnails(raw, max_pdf_pages, dpi=100)
            source_kind = "pdf_text"
            if info["page_count"] > max_pdf_pages:
                st.warning(f"PDF có {info['page_count']} trang, chỉ xử lý {max_pdf_pages} trang đầu.")
            for i, png in enumerate(page_pngs):
                norm, size = preprocess_pipeline(
                    png, max_side, do_crop=False, do_clahe=False, do_sharpen=False,
                )
                images_b64.append(encode_b64(norm))
                st.image(norm,
                         caption=f"Thumbnail trang {i+1}/{len(page_pngs)} · {size[0]}×{size[1]}px",
                         use_container_width=True)
        else:
            page_pngs, total = pdf_all_pages_to_pngs(raw, max_pdf_pages)
            source_kind = "pdf_image"
            if total > max_pdf_pages:
                st.warning(f"PDF có {total} trang, chỉ xử lý {max_pdf_pages} trang đầu.")
            for i, png in enumerate(page_pngs):
                norm, size = preprocess_pipeline(
                    png, max_side, do_crop=do_crop, do_clahe=do_clahe, do_sharpen=do_sharpen,
                )
                bs = blur_score(norm) if blur_check_on else None
                bs_str = f" · 🔍 sharpness={bs:.0f}" if bs is not None else ""
                st.image(norm,
                         caption=f"Trang {i+1}/{len(page_pngs)} · {size[0]}×{size[1]}px{bs_str}",
                         use_container_width=True)
                images_b64.append(encode_b64(norm))
                if bs is not None:
                    blur_scores.append(bs)
    else:
        source_kind = "image"
        norm, size = preprocess_pipeline(
            raw, max_side, do_crop=do_crop, do_clahe=do_clahe, do_sharpen=do_sharpen,
        )
        bs = blur_score(norm) if blur_check_on else None
        bs_str = f" · 🔍 sharpness={bs:.0f}" if bs is not None else ""
        st.image(norm,
                 caption=f"Ảnh menu · {size[0]}×{size[1]}px{bs_str}",
                 use_container_width=True)
        images_b64.append(encode_b64(norm))
        if bs is not None:
            blur_scores.append(bs)

    return images_b64, blur_scores, source_kind, text_per_page


# ============================================================
#                       MAIN APP
# ============================================================
def main():
    st.set_page_config(page_title="Menu → JSON POS | FC-first", layout="wide")
    _auth_gate()

    st.title("🍱 Menu → JSON POS · FC-first 2-call pipeline")
    st.caption("Bước 1: scan food_conditions (model rẻ) · Bước 2: build groups dùng whitelist (model mạnh)")

    # ========================================================
    #                       SIDEBAR
    # ========================================================
    with st.sidebar:
        st.header("🌐 Provider")
        provider = st.radio(
            "Nhà cung cấp",
            ["Anthropic (Claude)", "OpenAI (GPT)"],
            horizontal=True,
        )
        provider_key = "claude" if provider.startswith("Anthropic") else "openai"

        st.divider()
        st.header("🔑 API key")
        if provider_key == "claude":
            api_key_input = st.text_input(
                "ANTHROPIC_API_KEY", value="", type="password",
                help="Để trống nếu đã set trong .env / env",
            )
            if os.environ.get("ANTHROPIC_API_KEY") and not api_key_input:
                st.caption("✅ Dùng ANTHROPIC_API_KEY từ env / .env")
        else:
            api_key_input = st.text_input(
                "OPENAI_API_KEY", value="", type="password",
                help="Để trống nếu đã set trong .env / env",
            )
            if os.environ.get("OPENAI_API_KEY") and not api_key_input:
                st.caption("✅ Dùng OPENAI_API_KEY từ env / .env")

        st.divider()
        st.header("🤖 Model")
        cache_key = f"models_cache_{provider_key}"
        if st.button("🔄 Tải lại danh sách model từ API", use_container_width=True):
            st.session_state.pop(cache_key, None)

        if cache_key not in st.session_state:
            if provider_key == "claude":
                _client, _err = get_client(api_key_input)
                fb = CLAUDE_MODELS_FALLBACK
                lister = list_available_models
            else:
                _client, _err = get_openai_client(api_key_input)
                fb = OPENAI_MODELS_FALLBACK
                lister = list_available_openai_models
            if _client is not None:
                with st.spinner("Đang lấy danh sách model..."):
                    st.session_state[cache_key] = lister(_client)
            else:
                st.session_state[cache_key] = fb

        available_models = st.session_state[cache_key]
        cheap_models = [m for m in available_models if is_cheap_model(m)] or available_models

        if available_models in (CLAUDE_MODELS_FALLBACK, OPENAI_MODELS_FALLBACK):
            st.caption("⚠️ Đang dùng list mặc định (chưa kết nối được API).")
        else:
            st.caption(f"✅ {len(available_models)} model khả dụng trên account này")

        chosen_fc   = st.selectbox("Model bước 1 — FC scan (rẻ)",
                                   cheap_models, index=0)
        chosen_menu = st.selectbox("Model bước 2 — Menu build (mạnh)",
                                   available_models, index=0)

        st.divider()
        st.header("⚙️ Tùy chọn API")
        max_tokens_fc   = st.slider(
            "max_tokens (FC scan)", 1024, 8192, 4096, 256,
            help=("Reasoning model (o-series, gpt-5, opus-4-7) tiêu thinking tokens "
                  "trước khi emit tool_use. Kéo cao nếu Bước 1 báo 'không gọi tool'."),
        )
        max_tokens_menu = st.slider(
            "max_tokens (Menu build)", 2048, 16384, 8192, 512,
            help="Menu dài / nhiều combo → cần cao. Kéo lên nếu thấy cảnh báo truncate.",
        )
        max_side        = st.slider("Cạnh dài ảnh tối đa (px)", 800, 2400, RESIZE_MAX_SIDE, 100)
        max_pdf_pages   = st.slider("Số trang PDF tối đa", 1, 20, 5, 1)
        cache_prompt    = st.checkbox("Prompt caching (giảm $ rerun)", value=True)

        st.divider()
        st.header("🖼️ Tiền xử lý ảnh")
        do_crop    = st.checkbox("Auto-crop viền trắng", value=True,
                                 help="Giảm token cho ảnh có nhiều khoảng trắng. Skip tự động nếu menu đã fill khung.")
        do_clahe   = st.checkbox("CLAHE (tăng tương phản)", value=True,
                                 help="Tốt cho ảnh chụp ngược sáng / nền xám.")
        do_sharpen = st.checkbox("Unsharp mask (nếu ảnh mờ)", value=True,
                                 help="Chỉ chạy khi blur_score < 800.")
        blur_check_on = st.checkbox("Cảnh báo ảnh mờ trước khi gọi API", value=True)

        st.divider()
        if st.button("🗑️ Xóa lịch sử so sánh", use_container_width=True):
            st.session_state.pop("history", None)
            st.rerun()

    # ========================================================
    #                  UPLOAD IMAGE / PDF
    # ========================================================
    col_in, col_truth = st.columns([2, 1])

    images_b64: List[str] = []
    blur_scores: List[float] = []
    source_kind = "none"
    text_per_page: List[str] = []

    with col_in:
        st.subheader("📤 Ảnh / PDF menu")
        uploaded = st.file_uploader("Upload ảnh menu hoặc PDF",
                                    type=["png", "jpg", "jpeg", "webp", "pdf"])
        if uploaded:
            try:
                images_b64, blur_scores, source_kind, text_per_page = _process_uploaded_file(
                    uploaded, max_side, max_pdf_pages,
                    do_crop=do_crop, do_clahe=do_clahe, do_sharpen=do_sharpen,
                    blur_check_on=blur_check_on,
                )
            except Exception as e:
                st.error(f"Lỗi xử lý file: {e}")

        # ----- Blur evaluation -----
        blur_level = "ok"
        worst_bs: float = None
        if blur_check_on and blur_scores and source_kind != "pdf_text":
            worst_bs = min(blur_scores)
            if worst_bs < BLUR_BLOCK_THRESHOLD:
                blur_level = "block"
            elif worst_bs < BLUR_WARN_THRESHOLD:
                blur_level = "warn"
        elif blur_check_on and images_b64 and not blur_scores and source_kind != "pdf_text":
            st.caption("⚠️ Blur check không khả dụng (cv2 chưa cài).")

        blur_override = False
        if blur_level == "block":
            st.error(
                f"🚫 **Ảnh quá mờ** — sharpness thấp nhất = **{worst_bs:.0f}** "
                f"(ngưỡng tối thiểu **{BLUR_BLOCK_THRESHOLD:.0f}**).\n\n"
                "Model gần như chắc chắn sẽ đọc sai giá. **Hãy chụp lại**."
            )
            st.info("💡 Tips: tắt flash, ánh sáng tự nhiên, tay không rung, "
                    "chụp gần để chữ ≥ 20px, chụp thẳng, tránh bóng tay.")
        elif blur_level == "warn":
            st.warning(
                f"📷 **Ảnh hơi mờ** — sharpness = **{worst_bs:.0f}** "
                f"(ngưỡng an toàn **{BLUR_WARN_THRESHOLD:.0f}**).\n\n"
                "Khả năng model đọc sai chữ số nhỏ. Khuyến nghị chụp lại."
            )
            blur_override = st.checkbox(
                "Tôi hiểu rủi ro, vẫn muốn chạy (sai giá là trách nhiệm của tôi)",
                value=False,
            )

        if source_kind == "pdf_text" and text_per_page:
            with st.expander(f"📝 Văn bản trích từ PDF ({len(text_per_page)} trang)", expanded=False):
                for i, t in enumerate(text_per_page):
                    st.text_area(f"Trang {i+1}", value=t, height=160, key=f"pdf_text_{i}")

    with col_truth:
        st.subheader("✅ Đáp án (tùy chọn)")
        st.caption("Upload JSON đáp án để chấm điểm. Hỗ trợ format {groups:[...]} và legacy list phẳng.")
        truth_file = st.file_uploader("Ground truth JSON", type=["json"], key="truth")
        truth_items = None
        if truth_file:
            try:
                data = json.load(truth_file)
                if isinstance(data, dict) and "groups" in data:
                    truth_items = data
                    n = sum(len(g.get("foods", [])) for g in data["groups"])
                    st.success(f"Đáp án (nested) có {len(data['groups'])} group · {n} food")
                else:
                    truth_items = data if isinstance(data, list) else (data.get("items") or data.get("menu") or [])
                    st.success(f"Đáp án (flat) có {len(truth_items)} món")
            except Exception as e:
                st.error(f"JSON đáp án lỗi: {e}")

    with st.expander("📝 Prompts (chỉnh được)"):
        prompt_fc = st.text_area(
            "Bước 1 — FC scan prompt", value=FC_SCAN_PROMPT, height=240,
        )
        prompt_menu = st.text_area(
            "Bước 2 — Menu build prompt (`__FC_LIST_JSON__` sentinel)",
            value=MENU_BUILD_PROMPT, height=240,
        )

    # ========================================================
    #                       RUN BUTTON
    # ========================================================
    if "history" not in st.session_state:
        st.session_state["history"] = []

    run_disabled = (
        (not images_b64)
        or (blur_level == "block")
        or (blur_level == "warn" and not blur_override)
    )
    if not images_b64:
        run_label = "📤 Upload ảnh / PDF trước"
    elif blur_level == "block":
        run_label = "🚫 Ảnh quá mờ — không cho chạy"
    else:
        run_label = "🚀 Chạy 2-call pipeline"
    run = st.button(run_label, type="primary",
                    use_container_width=True, disabled=run_disabled)

    if run and images_b64:
        _run_pipeline(
            provider_key=provider_key, api_key_input=api_key_input,
            chosen_fc=chosen_fc, chosen_menu=chosen_menu,
            prompt_fc=prompt_fc, prompt_menu=prompt_menu,
            images_b64=images_b64, source_kind=source_kind,
            text_per_page=text_per_page,
            max_tokens_fc=max_tokens_fc, max_tokens_menu=max_tokens_menu,
            cache_prompt=cache_prompt,
            blur_level=blur_level, worst_bs=worst_bs,
        )

    # ========================================================
    #                    RESULT DISPLAY
    # ========================================================
    _render_results(truth_items)


# ============================================================
#                       ERROR EXPLAIN
# ============================================================
def _explain_no_tool_call(*, step_name: str, model: str, r: dict,
                          max_tokens: int, slider_label: str):
    """Render an actionable error when a step returned text instead of a tool
    call. The #1 cause is reasoning-model truncation: thinking tokens consume
    `max_tokens` before the model gets to emit `tool_use`."""
    stop  = r.get("stop_reason")
    usage = r.get("usage") or {}
    out_tok = usage.get("output_tokens", 0)
    raw     = (r.get("raw_text") or "").strip()
    is_reasoning = _is_reasoning_model(model)

    truncated = (
        stop in ("max_tokens", "length")
        or (max_tokens and out_tok >= max_tokens * 0.95)
    )

    st.error(f"🚫 **{step_name} không gọi tool** — model trả text thường thay vì tool_use.")

    diag = (
        f"- model: `{model}`{' (reasoning)' if is_reasoning else ''}\n"
        f"- stop_reason: `{stop}`\n"
        f"- output_tokens: **{out_tok}** / max_tokens **{max_tokens}**\n"
        f"- thời gian: {r.get('sec', 0):.1f}s"
    )
    st.markdown(diag)

    if truncated:
        suggested = max(8192, out_tok * 2) if max_tokens else 8192
        st.warning(
            f"🪙 **Output bị truncate** — đụng (hoặc gần đụng) trần `max_tokens`. "
            + (
                f"Reasoning model ({model}) tiêu thinking tokens TRƯỚC khi emit "
                "`tool_use` — nếu `max_tokens` cạn trong lúc thinking, response "
                "không có tool block.\n\n"
                if is_reasoning else ""
            )
            + f"**Fix:** kéo slider `{slider_label}` lên **{suggested}** rồi chạy lại."
        )
    elif is_reasoning:
        st.warning(
            f"Reasoning model ({model}) đôi khi 'cãi' `tool_choice=forced` với "
            "vision + long context. Thử model khác (sonnet / gpt-4.1) ở step này, "
            "hoặc kéo `max_tokens` lên cao."
        )
    else:
        st.warning(
            "Model trả text mà không có lý do truncation rõ ràng. "
            "Thử rerun, đổi model, hoặc kiểm tra raw text bên dưới."
        )

    if raw:
        with st.expander(f"📝 Raw text model trả về ({len(raw)} ký tự)", expanded=False):
            st.text(raw[:5000])
    else:
        st.caption("(Model không trả về text nào cả — pure empty response.)")


# ============================================================
#                       PIPELINE RUN
# ============================================================
def _run_pipeline(*, provider_key, api_key_input,
                  chosen_fc, chosen_menu,
                  prompt_fc, prompt_menu,
                  images_b64, source_kind, text_per_page,
                  max_tokens_fc, max_tokens_menu, cache_prompt,
                  blur_level, worst_bs):
    if provider_key == "claude":
        client, err = get_client(api_key_input)
    else:
        client, err = get_openai_client(api_key_input)
    if err:
        st.error(err)
        return

    # Blur warning injection (applies to BOTH steps).
    warning_block = ""
    if blur_level == "warn" and worst_bs is not None:
        warning_block = (
            f"⚠️ SYSTEM WARNING: input image has sharpness={worst_bs:.0f} "
            f"(safe threshold {BLUR_WARN_THRESHOLD:.0f}). "
            f"If ANY price digit is not 100% clear, PREFER report_unreadable.\n\n"
        )

    # Text-PDF prefix when applicable.
    text_pdf_block = ""
    if source_kind == "pdf_text" and text_per_page:
        text_blob = "\n\n".join(
            f"--- Page {i+1} ---\n{t}" for i, t in enumerate(text_per_page)
        )
        text_pdf_block = (
            TEXT_PDF_PROMPT_PREFIX
            + "\n=== EXTRACTED PDF TEXT ===\n"
            + text_blob
            + "\n=== END EXTRACTED TEXT ===\n\n"
        )

    full_fc_prompt = warning_block + text_pdf_block + prompt_fc

    # ---------- Call 1: FC scan ----------
    fc_spinner = (
        f"⏳ Bước 1/2 — Scan food_conditions bằng {chosen_fc}..."
    )
    with st.spinner(fc_spinner):
        print(f"[{datetime.now():%H:%M:%S}] → FC scan: {chosen_fc}", flush=True)
        r1 = call_fc_scan(client, chosen_fc, full_fc_prompt,
                          image_b64_list=images_b64,
                          max_tokens=max_tokens_fc,
                          cache_prompt=cache_prompt)
        print(f"[{datetime.now():%H:%M:%S}] ← FC scan {chosen_fc} in {r1['sec']:.1f}s "
              f"· tool={r1['tool_name']} · err={r1['error']}", flush=True)

    if r1["error"]:
        st.error(f"Lỗi bước 1: {r1['error']}")
        return
    if r1["tool_name"] == "report_unreadable":
        st.session_state["history"].append(_history_entry_refusal(
            step="fc", r=r1, model_fc=chosen_fc, model_menu=chosen_menu,
            images_b64=images_b64, source_kind=source_kind,
            max_tokens_fc=max_tokens_fc, max_tokens_menu=max_tokens_menu,
        ))
        return
    if r1["tool_name"] != "submit_food_conditions":
        _explain_no_tool_call(
            step_name="Bước 1 (FC scan)",
            model=chosen_fc,
            r=r1,
            max_tokens=max_tokens_fc,
            slider_label="max_tokens (FC scan)",
        )
        return

    fc_list = (r1["tool_input"] or {}).get("food_conditions") or []
    st.success(f"✅ Bước 1 — Tìm thấy {len(fc_list)} food_conditions.")

    # ---------- Call 2: menu build PER PAGE (parallel) ----------
    fc_list_json = json.dumps(fc_list, ensure_ascii=False, indent=2)
    base_menu_prompt = prompt_menu.replace("__FC_LIST_JSON__", fc_list_json)
    n_pages = len(images_b64)

    def _build_page_prompt(page_idx: int) -> str:
        """Per-page prompt: warning + page-specific text (if text-PDF) +
        menu prompt with FC whitelist baked in + page locator hint."""
        per_page_text = ""
        if source_kind == "pdf_text" and text_per_page and page_idx < len(text_per_page):
            per_page_text = (
                TEXT_PDF_PROMPT_PREFIX
                + f"\n=== EXTRACTED TEXT (page {page_idx+1}/{n_pages}) ===\n"
                + text_per_page[page_idx]
                + "\n=== END EXTRACTED TEXT ===\n\n"
            )
        page_locator = (
            f"\n\n=== TASK ===\nYou are processing ONLY page {page_idx+1} of "
            f"{n_pages}. Build groups for THIS page only. Other pages are handled "
            f"in separate calls. Group names may repeat across pages — that's "
            f"expected and will be merged client-side.\n"
        )
        return warning_block + per_page_text + base_menu_prompt + page_locator

    def _run_one_page(page_idx: int):
        prompt = _build_page_prompt(page_idx)
        print(f"[{datetime.now():%H:%M:%S}] → menu build page {page_idx+1}/{n_pages}: "
              f"{chosen_menu}", flush=True)
        r = call_menu_build(
            client, chosen_menu, prompt,
            image_b64_list=[images_b64[page_idx]],   # ONLY this page
            max_tokens=max_tokens_menu,
            cache_prompt=cache_prompt,
        )
        print(f"[{datetime.now():%H:%M:%S}] ← menu build page {page_idx+1}/{n_pages} "
              f"in {r['sec']:.1f}s · tool={r['tool_name']} · err={r['error']}",
              flush=True)
        return page_idx, r

    workers = min(n_pages, MAX_PARALLEL_MENU_CALLS)
    menu_spinner = (
        f"⏳ Bước 2/2 — {chosen_menu} build {n_pages} trang "
        + (f"song song (max {workers} cùng lúc)" if n_pages > 1 else "")
        + (" · reasoning model" if _is_reasoning_model(chosen_menu) else "")
    )

    page_results: List[dict] = [None] * n_pages  # type: ignore
    with st.spinner(menu_spinner):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for page_idx, r in pool.map(_run_one_page, range(n_pages)):
                page_results[page_idx] = r

    # Aggregate per-page usage / sec / status into one history entry.
    def _sum_usage(rs):
        keys = ("input_tokens", "output_tokens", "cache_read", "cache_create")
        out = {k: 0 for k in keys}
        for r in rs:
            u = r.get("usage") or {}
            for k in keys:
                out[k] += u.get(k, 0) or 0
        return out

    menu_usage = _sum_usage(page_results)
    menu_sec_total = sum(r["sec"] for r in page_results)
    page_tools = [r["tool_name"] for r in page_results]
    page_errors = [r["error"] for r in page_results if r["error"]]

    # Merge page-level groups → one groups[] dict (dedupe by group name).
    merged_payload = merge_groups_payloads(
        [r["tool_input"] for r in page_results
         if r["tool_name"] == "submit_menu_groups" and isinstance(r["tool_input"], dict)]
    )

    # First page that hit truncation (surfaces in UI warning).
    truncated_stop = next(
        (r.get("stop_reason") for r in page_results
         if r.get("stop_reason") in ("max_tokens", "length")),
        None,
    )

    # Overall menu tool_name policy:
    #   - ANY page got submit_menu_groups → success (partial OK)
    #   - else if ALL pages report_unreadable → refusal
    #   - else → mixed/wrong (will show error)
    if any(t == "submit_menu_groups" for t in page_tools):
        overall_menu_tool = "submit_menu_groups"
    elif all(t == "report_unreadable" for t in page_tools):
        overall_menu_tool = "report_unreadable"
    else:
        overall_menu_tool = None

    refusal_input = next(
        (r["tool_input"] for r in page_results
         if r["tool_name"] == "report_unreadable"),
        None,
    )
    menu_tool_input = (
        merged_payload if overall_menu_tool == "submit_menu_groups" else refusal_input
    )

    st.session_state["history"].append({
        "label": f"{chosen_fc} → {chosen_menu}",
        "provider_fc":   get_provider(chosen_fc),
        "provider_menu": get_provider(chosen_menu),
        "model_fc":   chosen_fc,
        "model_menu": chosen_menu,
        # Call 1
        "fc_tool_name":  r1["tool_name"],
        "fc_tool_input": r1["tool_input"],
        "fc_usage":      r1["usage"],
        "fc_sec":        r1["sec"],
        "fc_stop_reason":   r1.get("stop_reason"),
        "fc_max_tokens":    max_tokens_fc,
        # Call 2 (aggregated across pages)
        "menu_tool_name":  overall_menu_tool,
        "menu_tool_input": menu_tool_input,
        "menu_usage":      menu_usage,
        "menu_sec":        menu_sec_total,
        "menu_err":        "; ".join(page_errors) if page_errors else None,
        "menu_stop_reason": truncated_stop,
        "menu_max_tokens":  max_tokens_menu,
        "menu_raw_text":    "\n---\n".join(
            f"[page {i+1}] {(r.get('raw_text') or '')[:1500]}"
            for i, r in enumerate(page_results) if r.get("raw_text")
        ),
        # Per-page breakdown for UI display
        "menu_per_page": [
            {
                "page":        i + 1,
                "tool":        r["tool_name"],
                "sec":         r["sec"],
                "err":         r["error"],
                "stop_reason": r.get("stop_reason"),
                "usage":       r["usage"],
                "n_groups":    (
                    len((r.get("tool_input") or {}).get("groups") or [])
                    if r["tool_name"] == "submit_menu_groups" else 0
                ),
            }
            for i, r in enumerate(page_results)
        ],
        # Aggregate
        "sec":         r1["sec"] + menu_sec_total,
        "n_pages":     n_pages,
        "source_kind": source_kind,
        "time":        datetime.now().strftime("%H:%M:%S"),
    })


def _history_entry_refusal(*, step, r, model_fc, model_menu,
                           images_b64, source_kind,
                           max_tokens_fc, max_tokens_menu):
    """Build a history entry when Call 1 returned report_unreadable
    (Call 2 was skipped). `step` ∈ {'fc'}."""
    return {
        "label": f"{model_fc} → (skipped)",
        "provider_fc":   get_provider(model_fc),
        "provider_menu": get_provider(model_menu),
        "model_fc":   model_fc,
        "model_menu": model_menu,
        "fc_tool_name":  r["tool_name"],
        "fc_tool_input": r["tool_input"],
        "fc_usage":      r["usage"],
        "fc_sec":        r["sec"],
        "fc_stop_reason": r.get("stop_reason"),
        "fc_max_tokens":  max_tokens_fc,
        "menu_tool_name":  None,
        "menu_tool_input": None,
        "menu_usage":      None,
        "menu_sec":        0.0,
        "menu_err":        None,
        "menu_stop_reason": None,
        "menu_max_tokens":  max_tokens_menu,
        "menu_raw_text":    "",
        "menu_per_page":   [],
        "sec":         r["sec"],
        "n_pages":     len(images_b64),
        "source_kind": source_kind,
        "time":        datetime.now().strftime("%H:%M:%S"),
    }


# ============================================================
#                       RESULT RENDERING
# ============================================================
def _render_results(truth_items):
    history = st.session_state.get("history", [])
    if not history:
        return

    st.divider()
    # -------- Session stats --------
    st.subheader("📈 Thống kê session")
    total_cost = sum(_compute_run_cost(h) for h in history)
    total_time = sum(h.get("sec", 0) for h in history)
    total_runs = len(history)
    success_runs = sum(1 for h in history
                       if h.get("menu_tool_name") == "submit_menu_groups" and not h.get("menu_err"))

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Lần chạy", total_runs, f"✅ {success_runs} thành công")
    s2.metric("Tổng $ session", f"${total_cost:.4f}")
    s3.metric("Tổng thời gian", f"{total_time:.1f}s",
              f"⌀ {total_time/total_runs:.1f}s/lần" if total_runs else None)
    avg_cost = total_cost / total_runs if total_runs else 0
    s4.metric("Trung bình $/lần", f"${avg_cost:.4f}")

    st.divider()
    last = history[-1]
    st.subheader(f"📋 Kết quả mới nhất — {last['label']}")

    cost_last = _compute_run_cost(last)
    src_str  = f" · src={last.get('source_kind','?')}"
    cost_str = f" · 💵 ~${cost_last:.4f}" if cost_last else ""
    meta = (f"⏱️ {last['sec']:.1f}s ({last['fc_sec']:.1f}s + {last['menu_sec']:.1f}s)"
            f"{src_str} · {last['n_pages']} trang · {last['time']}{cost_str}")
    st.caption(meta)

    # ----- Step 1 status -----
    fc_tool = last.get("fc_tool_name")
    if fc_tool == "report_unreadable":
        ti = last.get("fc_tool_input") or {}
        st.error("📷 **Bước 1: Ảnh không đọc được — model từ chối**")
        st.warning(f"**Reason:** {ti.get('reason','—')}\n\n**Suggestion:** {ti.get('suggestion','—')}")
        return

    # Per-page breakdown for Bước 2.
    per_page = last.get("menu_per_page") or []
    if len(per_page) > 1:
        ok_pages = sum(1 for pp in per_page if pp["tool"] == "submit_menu_groups")
        refused  = sum(1 for pp in per_page if pp["tool"] == "report_unreadable")
        failed   = sum(1 for pp in per_page if pp["tool"] not in
                       ("submit_menu_groups", "report_unreadable"))
        with st.expander(
            f"📄 Bước 2 — chi tiết {len(per_page)} trang "
            f"(✅ {ok_pages} · 📷 {refused} · ❌ {failed})",
            expanded=False,
        ):
            st.dataframe([
                {
                    "Page":    pp["page"],
                    "Tool":    pp["tool"] or "(none)",
                    "s":       round(pp["sec"], 1),
                    "Groups":  pp["n_groups"],
                    "In tok":  (pp["usage"] or {}).get("input_tokens", 0),
                    "Out tok": (pp["usage"] or {}).get("output_tokens", 0),
                    "Cache":   (pp["usage"] or {}).get("cache_read", 0),
                    "Stop":    pp["stop_reason"] or "—",
                    "Error":   (pp["err"] or "")[:60],
                } for pp in per_page
            ], use_container_width=True, hide_index=True)

    fc_list = (last.get("fc_tool_input") or {}).get("food_conditions") or []
    with st.expander(f"🔍 Bước 1 — {len(fc_list)} food_conditions từ FC scan", expanded=False):
        if fc_list:
            st.dataframe([{
                "Name":       fc.get("name"),
                "Base price": fc.get("base_price"),
                "PLU":        fc.get("plu") or "",
                "Status":     fc.get("status"),
            } for fc in fc_list], use_container_width=True, hide_index=True)
        else:
            st.info("Menu này không có modifier nào.")

    # ----- Step 2 -----
    if last.get("menu_err"):
        st.error(f"Lỗi bước 2: {last['menu_err']}")
        return
    menu_tool = last.get("menu_tool_name")
    if menu_tool == "report_unreadable":
        ti = last.get("menu_tool_input") or {}
        st.error("📷 **Bước 2: Ảnh không đọc được — model từ chối**")
        st.warning(f"**Reason:** {ti.get('reason','—')}\n\n**Suggestion:** {ti.get('suggestion','—')}")
        return
    if menu_tool != "submit_menu_groups":
        # Reconstruct the result dict shape that _explain_no_tool_call expects.
        _explain_no_tool_call(
            step_name="Bước 2 (Menu build)",
            model=last["model_menu"],
            r={
                "tool_name":   menu_tool,
                "raw_text":    last.get("menu_raw_text") or "",
                "usage":       last.get("menu_usage"),
                "stop_reason": last.get("menu_stop_reason"),
            },
            max_tokens=last.get("menu_max_tokens"),
            slider_label="max_tokens (Menu build)",
        )
        return

    raw_menu = merge_fc_and_groups(fc_list, last.get("menu_tool_input") or {})
    valid_menu, errors = validate_menu(raw_menu)
    counts_raw   = count_menu(raw_menu)
    counts_valid = count_menu(valid_menu)

    # ⚠️ Truncation detection on the menu step (the heavy output).
    # Per-page truncation detection (each page has its own max_tokens limit;
    # aggregate output_tokens vs per-call max would yield false positives).
    max_tok = last.get("menu_max_tokens")
    truncated_pages = []
    for pp in last.get("menu_per_page") or []:
        pp_out = (pp.get("usage") or {}).get("output_tokens", 0)
        pp_stop = pp.get("stop_reason")
        if pp_stop in ("max_tokens", "length") or (max_tok and pp_out >= max_tok * 0.98):
            truncated_pages.append({"page": pp["page"], "out": pp_out, "stop": pp_stop})
    if truncated_pages:
        pages_str = ", ".join(f"trang {t['page']} ({t['out']} tok, stop={t['stop']})"
                              for t in truncated_pages)
        worst = max(t["out"] for t in truncated_pages)
        st.error(
            f"🚫 **{len(truncated_pages)}/{len(last.get('menu_per_page') or [])} trang "
            f"bị truncate** — {pages_str}.\n\n"
            f"**Fix:** kéo `max_tokens (Menu build)` lên **{max(12000, worst * 2)}** rồi chạy lại."
        )

    # ----- Metrics -----
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Groups",     counts_valid["groups"],          f"raw {counts_raw['groups']}")
    c2.metric("Foods",      counts_valid["foods"],           f"raw {counts_raw['foods']}")
    c3.metric("Choose",     counts_valid["choose_kinds"],    f"subs {counts_valid['sub_foods']}")
    c4.metric("FoodConds",  counts_valid["food_conditions"], f"raw {counts_raw['food_conditions']}")
    c5.metric("Options",    counts_valid["option_groups"],   f"links {counts_valid['option_links']}")
    c6.metric("Errors",     len(errors))

    if errors:
        with st.expander(f"⚠️ {len(errors)} item không pass validation", expanded=False):
            st.dataframe(errors, use_container_width=True)

    if truth_items and counts_valid["foods"]:
        sc = score_against_truth(valid_menu, truth_items)
        if sc:
            cc1, cc2 = st.columns(2)
            cc1.metric("Khớp tên", f"{sc['name_recall']}%",
                       f"{sc['name_match']}/{sc['n_truth']}")
            cc2.metric("Đúng giá", f"{sc['price_acc']}%",
                       f"{sc['price_match']}/{sc['n_truth']}")
            if sc["missing"]:
                st.caption("❌ Thiếu: " + ", ".join(sc["missing"][:15]))
            if sc["extra"]:
                st.caption("➕ Dư: " + ", ".join(sc["extra"][:15]))

    # ----- Detail view: 3 tabs -----
    tab_menu, tab_fc, tab_json = st.tabs([
        "🌳 Menu tree", "🧂 Food Conditions", "🧾 Raw JSON",
    ])
    fc_lookup = {x.get("name", "").lower(): x
                 for x in valid_menu.get("food_conditions", [])}

    def _render_options(food: dict, indent: str = ""):
        for og in food.get("options") or []:
            sel = "single" if og.get("type") == 0 else "multi"
            req = "required" if og.get("option") == 1 else "optional"
            st.caption(f"{indent}🍽 **{og.get('name','')}** ({sel}, {req})")
            rows = []
            for fd in og.get("food_datas") or []:
                nm = fd.get("name_food", "")
                fc = fc_lookup.get(nm.lower())
                canonical = fc.get("base_price") if fc else "⚠ orphan"
                rows.append({
                    "Name":              nm,
                    "Price (this food)": fd.get("price"),
                    "Canonical price":   canonical,
                    "Required":          fd.get("required") or False,
                    "PLU":               fd.get("plu") or "",
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

    with tab_menu:
        for g in valid_menu.get("groups", []):
            st.markdown(f"### 📂 {g.get('name', '—')}")
            if g.get("description"):
                st.caption(g["description"])
            for f in g.get("foods", []):
                type_lbl = TYPE_LABELS.get(f.get("type"), "?")
                kind_lbl = KIND_LABELS.get(f.get("kind"), "?")
                is_choose = f.get("kind") == KIND_CHOOSE
                icon = "📋" if is_choose else "🍽"
                price_str = (
                    f"{f.get('price_in', 0)} / {f.get('price_out', 0)}"
                    if not is_choose else "— (CHOOSE parent, sub-foods below)"
                )
                st.markdown(
                    f"**{icon} {f.get('name','')}**  "
                    f"`{type_lbl}/{kind_lbl}` · price_in/out = {price_str}"
                    + (f" · PLU `{f['plu']}`" if f.get("plu") else "")
                )
                if f.get("description"):
                    st.caption(f.get("description"))

                if is_choose and f.get("foods"):
                    st.caption(f"📋 Sub-foods ({len(f['foods'])} variants)")
                    for child in f["foods"]:
                        child_type = TYPE_LABELS.get(child.get("type"), "?")
                        st.markdown(
                            f"&nbsp;&nbsp;&nbsp;&nbsp;• **{child.get('name','')}** "
                            f"`{child_type}` · {child.get('price_in')} / {child.get('price_out')}"
                            + (f" · PLU `{child['plu']}`" if child.get('plu') else ""),
                            unsafe_allow_html=True,
                        )
                        _render_options(child, indent="&nbsp;&nbsp;&nbsp;&nbsp;")

                _render_options(f)
                st.divider()

    with tab_fc:
        fcs = valid_menu.get("food_conditions") or []
        if not fcs:
            st.info("Menu này không có food_conditions nào.")
        else:
            st.caption(f"📌 {len(fcs)} item — sẵn sàng POST /v1/foodcondition/create")
            st.dataframe([{
                "Name":       x.get("name"),
                "Base price": x.get("base_price"),
                "PLU":        x.get("plu") or "",
                "Status":     x.get("status"),
            } for x in fcs], use_container_width=True, hide_index=True)

    with tab_json:
        st.json(valid_menu, expanded=False)

    # ----- Download -----
    colx, coly, colz = st.columns(3)
    colx.download_button(
        "⬇️ JSON (validated, full)",
        data=json.dumps(valid_menu, ensure_ascii=False, indent=2),
        file_name=f"menu_{last['model_menu']}_{last['time'].replace(':','-')}.json",
        mime="application/json", use_container_width=True,
    )
    coly.download_button(
        "⬇️ JSON (food_conditions only)",
        data=json.dumps(valid_menu.get("food_conditions", []), ensure_ascii=False, indent=2),
        file_name=f"food_conditions_{last['time'].replace(':','-')}.json",
        mime="application/json", use_container_width=True,
    )
    colz.download_button(
        "⬇️ JSON (raw từ model)",
        data=json.dumps(raw_menu, ensure_ascii=False, indent=2),
        file_name=f"menu_raw_{last['time'].replace(':','-')}.json",
        mime="application/json", use_container_width=True,
    )

    # -------- Cumulative comparison --------
    if len(history) > 1:
        st.divider()
        st.subheader("📊 So sánh tất cả các lần chạy")
        summary = []
        for h in history:
            fc_list_h = (h.get("fc_tool_input") or {}).get("food_conditions") or []
            menu_payload = h.get("menu_tool_input") or {}
            raw_menu_h = merge_fc_and_groups(fc_list_h, menu_payload)
            valid_h, errs = validate_menu(raw_menu_h)
            counts = count_menu(valid_h)

            if h.get("menu_err"):
                status = "❌ Lỗi API"
            elif h.get("fc_tool_name") == "report_unreadable":
                status = "📷 FC: mờ"
            elif h.get("menu_tool_name") == "report_unreadable":
                status = "📷 Menu: mờ"
            elif h.get("menu_tool_name") == "submit_menu_groups":
                status = "✅" if not errs else f"⚠️ {len(errs)} lỗi"
            else:
                status = "❌ Tool?"

            row = {
                "Lúc": h["time"],
                "FC model":   h["model_fc"],
                "Menu model": h["model_menu"],
                "Src": h.get("source_kind", "?"),
                "Trang": h.get("n_pages", 1),
                "s (FC+menu)": f"{h['fc_sec']:.1f}+{h['menu_sec']:.1f}",
                "Trạng thái": status,
                "FCs":     counts["food_conditions"],
                "Groups":  counts["groups"],
                "Foods":   counts["foods"],
                "Choose":  counts["choose_kinds"],
                "Options": counts["option_groups"],
            }
            cost = _compute_run_cost(h)
            if cost:
                row["$"] = f"{cost:.4f}"
            if truth_items and counts["foods"]:
                sc = score_against_truth(valid_h, truth_items)
                if sc:
                    row["Khớp tên %"] = sc["name_recall"]
                    row["Đúng giá %"] = sc["price_acc"]
            summary.append(row)
        st.dataframe(summary, use_container_width=True)

        st.download_button(
            "⬇️ Tải lịch sử session (JSON)",
            data=json.dumps(summary, ensure_ascii=False, indent=2),
            file_name=f"session_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
        )

    st.divider()
    with st.expander("ℹ️ Production notes"):
        st.markdown("""
- **FC-first 2-call**: Bước 1 (model rẻ) scan beilage modifiers → Bước 2 (model mạnh) build menu. Giảm orphan, tăng accuracy.
- **Schema CHOOSE-aware**: kind=1 COMMON với beilages, kind=5 CHOOSE với sub-foods (mỗi sub-food kind=1, no nesting). Khớp Food model của hq-qrcode-admin.
- **Decision rule**: header CÓ giá → kind=1 + beilage. Header KHÔNG giá → kind=5 + sub-foods.
- **PDF text-detect**: PDF có text layer → trích text + thumbnail thấp DPI → tiết kiệm ~80% input token.
- **Per-page parallel** Bước 2: PDF nhiều trang gọi song song max 4 concurrent.
- **Preprocessing**: auto-crop, CLAHE, unsharp mask (chỉ khi mờ).
- **Tool Use forced** ở cả 2 step: model bắt buộc gọi submit_* hoặc report_unreadable.
- **Validation**: orphan check (name_food → food_conditions), autofill safety net, no nested CHOOSE.
- **Timeout 300s, retry 3x** cho Claude. OpenAI timeout 300s, retry 1x.
""")


if __name__ == "__main__":
    main()
