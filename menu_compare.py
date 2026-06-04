"""
Menu → JSON POS | So sánh model qua Ollama
-------------------------------------------------
Web app Streamlit để test & so sánh nhiều model Ollama (VL + text)
trên bài toán đọc menu (ảnh/PDF) -> JSON chuẩn POS.

Chạy:
    pip install streamlit requests pillow pymupdf
    streamlit run menu_compare.py

Yêu cầu: máy chạy Ollama (máy mạnh) đã bật OLLAMA_HOST=0.0.0.0:11434
và laptop join cùng mạng ZeroTier, gọi được tới IP máy mạnh.
"""

import streamlit as st
import requests
import base64
import json
import time
import io
from datetime import datetime

# ----------------------------- Cấu hình ----------------------------------
st.set_page_config(page_title="Menu → JSON POS | So sánh model", layout="wide")

DEFAULT_SERVER = "http://192.168.192.216:11434"

# Prompt mặc định cho bài toán menu -> JSON POS (theo schema trong tài liệu)
DEFAULT_PROMPT = """Bạn là hệ thống đọc menu nhà hàng và xuất dữ liệu cho POS.
Đọc toàn bộ menu trong ảnh và xuất ra MỘT mảng JSON. Mỗi món gồm các trường:
- name: tên món (giữ nguyên ngôn ngữ gốc)
- category: nhóm món (ví dụ "Sushi Roll", "Appetizer", "Drink"...)
- type: một trong [main_item, side_item, sushi_main, sushi_side, topping, size, combo]
- price: giá dạng số (number), không kèm ký hiệu tiền tệ
- printer: máy in bếp phù hợp (ví dụ "sushi_bar", "hot_kitchen", "bar"); để "" nếu không rõ
- modifiers: mảng các tùy chọn thêm, mỗi cái gồm name và price (số). Để [] nếu không có
- tags: mảng nhãn ngắn mô tả món

CHỈ trả về JSON hợp lệ (một mảng các object), KHÔNG kèm giải thích, KHÔNG dùng markdown."""

OCR_ONLY_PROMPT = """Đọc và trích xuất TOÀN BỘ chữ trong ảnh menu này.
Giữ nguyên bố cục theo dòng: tên món, giá, mô tả, nhóm món.
Chỉ trả về văn bản đã đọc, không giải thích."""

# ----------------------------- Hàm tiện ích -------------------------------

def get_models(server):
    """Lấy danh sách model có trên server Ollama."""
    try:
        r = requests.get(f"{server}/api/tags", timeout=10)
        r.raise_for_status()
        data = r.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        return {"error": str(e)}


def is_vision_model(name):
    """Đoán model có nhìn ảnh được không (theo tên)."""
    n = name.lower()
    return any(k in n for k in ["vl", "vision", "llava", "bakllava", "moondream", "minicpm-v"])


def call_generate(server, model, prompt, images=None, force_json=True, timeout=600):
    """Gọi /api/generate. images: list base64. Trả về (text, seconds, error)."""
    payload = {"model": model, "prompt": prompt, "stream": False}
    if images:
        payload["images"] = images
    if force_json:
        payload["format"] = "json"
    t0 = time.time()
    try:
        r = requests.post(f"{server}/api/generate", json=payload, timeout=timeout)
        r.raise_for_status()
        elapsed = time.time() - t0
        return r.json().get("response", ""), elapsed, None
    except Exception as e:
        return "", time.time() - t0, str(e)


def pdf_first_page_to_png(file_bytes):
    """Chuyển trang đầu PDF thành PNG bytes. Cần pymupdf."""
    import fitz  # pymupdf
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=200)
    return pix.tobytes("png")


def normalize_image(raw_bytes, max_side=1600):
    """Mở ảnh bất kỳ, chuyển RGB, thu nhỏ nếu cạnh dài > max_side, xuất PNG sạch.
    Giúp tránh lỗi định dạng và giảm tải VRAM cho model VL.
    Trả về (png_bytes, (w, h))."""
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


def try_parse_json(text):
    """Cố parse JSON từ output (kể cả khi dính markdown fence)."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned)
    except Exception:
        # thử tìm đoạn [ ... ] hoặc { ... } đầu tiên
        for op, cl in [("[", "]"), ("{", "}")]:
            i, j = cleaned.find(op), cleaned.rfind(cl)
            if i != -1 and j != -1 and j > i:
                try:
                    return json.loads(cleaned[i:j + 1])
                except Exception:
                    pass
    return None


def normalize_items(parsed):
    """Đưa output về list các món để chấm điểm. Chấp nhận cả list lẫn {menu:[...]}."""
    if parsed is None:
        return None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ["menu", "items", "data", "dishes"]:
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        # dict đơn lẻ -> coi như 1 món
        return [parsed]
    return None


def score_against_truth(pred_items, truth_items):
    """Chấm điểm đơn giản: khớp theo tên (không phân biệt hoa thường) + đúng giá.
    Trả về dict điểm."""
    if pred_items is None:
        return {"valid_json": False}

    def norm_name(x):
        return str(x.get("name", "")).strip().lower()

    pred_map = {norm_name(it): it for it in pred_items if isinstance(it, dict)}
    truth_map = {norm_name(it): it for it in truth_items if isinstance(it, dict)}

    matched_names = set(pred_map) & set(truth_map)
    price_ok = 0
    for nm in matched_names:
        try:
            if abs(float(pred_map[nm].get("price", -1)) - float(truth_map[nm].get("price", -2))) < 0.01:
                price_ok += 1
        except Exception:
            pass

    total_truth = len(truth_map) or 1
    return {
        "valid_json": True,
        "n_pred": len(pred_items),
        "n_truth": len(truth_items),
        "name_match": len(matched_names),
        "name_recall": round(len(matched_names) / total_truth * 100, 1),
        "price_match": price_ok,
        "price_acc": round(price_ok / total_truth * 100, 1),
        "missing": sorted(set(truth_map) - set(pred_map)),
        "extra": sorted(set(pred_map) - set(truth_map)),
    }


# ----------------------------- Giao diện ----------------------------------

st.title("🍱 Menu → JSON POS")
st.caption("Chạy từng model Ollama để đọc menu nhà hàng, lưu lại và so sánh độ chính xác")

with st.sidebar:
    st.header("⚙️ Kết nối")
    server = st.text_input("Ollama server (IP ZeroTier máy mạnh)", value=DEFAULT_SERVER)

    if st.button("🔄 Tải danh sách model", use_container_width=True):
        st.session_state.pop("models_cache", None)

    if "models_cache" not in st.session_state:
        with st.spinner("Đang lấy danh sách model..."):
            st.session_state["models_cache"] = get_models(server)
    models = st.session_state["models_cache"]

    if isinstance(models, dict) and "error" in models:
        st.error(f"Không kết nối được:\n{models['error']}")
        st.info("Kiểm tra: máy mạnh bật chưa, OLLAMA_HOST=0.0.0.0:11434, mạng ZeroTier thông (ping được IP), firewall mở cổng 11434.")
        models = []
    elif not models:
        st.warning("Server chưa có model nào.")
    else:
        st.success(f"Tìm thấy {len(models)} model")
        vl = [m for m in models if is_vision_model(m)]
        txt = [m for m in models if not is_vision_model(m)]
        st.caption(f"👁️ Vision: {', '.join(vl) if vl else '—'}")
        st.caption(f"📝 Text: {', '.join(txt) if txt else '—'}")

    st.divider()
    st.header("🧪 Pipeline")
    pipeline = st.radio(
        "Chọn cách chạy",
        [
            "VL gộp (ảnh → JSON, 1 bước)",
            "Tách 2 bước (VL OCR → text model xuất JSON)",
        ],
    )

    vl_models = [m for m in models if is_vision_model(m)] if models else []
    txt_models = [m for m in models if not is_vision_model(m)] if models else []

    # Chọn 1 model mỗi lần chạy. Kết quả được lưu lại để so sánh giữa các lần.
    if pipeline.startswith("VL gộp"):
        chosen_vl = st.selectbox("Model VL", vl_models) if vl_models else None
        chosen_txt = None
    else:
        chosen_vl = st.selectbox("Model VL (bước OCR)", vl_models) if vl_models else None
        chosen_txt = st.selectbox("Model text (bước xuất JSON)", txt_models) if txt_models else None

    force_json = st.checkbox("Ép Ollama xuất JSON (format=json)", value=True)
    max_side = st.slider("Cạnh dài ảnh tối đa (px) — giảm nếu máy yếu/hết VRAM",
                         min_value=800, max_value=2400, value=1600, step=200)

    st.divider()
    if st.button("🗑️ Xóa lịch sử so sánh", use_container_width=True):
        st.session_state.pop("history", None)
        st.rerun()

# Khu vực chính
col_in, col_truth = st.columns([2, 1])

with col_in:
    st.subheader("📤 Ảnh / PDF menu")
    uploaded = st.file_uploader("Upload ảnh menu hoặc PDF", type=["png", "jpg", "jpeg", "webp", "pdf"])
    img_b64 = None
    if uploaded:
        raw = uploaded.read()
        try:
            if uploaded.type == "application/pdf":
                png = pdf_first_page_to_png(raw)              # PDF -> PNG (trang đầu)
                png, size = normalize_image(png, max_side)    # chuẩn hóa + thu nhỏ
                cap = f"Trang đầu PDF · {size[0]}×{size[1]}px"
            else:
                png, size = normalize_image(raw, max_side)    # ảnh thường: chuẩn hóa + thu nhỏ
                cap = f"Ảnh menu · {size[0]}×{size[1]}px"
            img_b64 = base64.b64encode(png).decode()
            st.image(png, caption=cap, use_container_width=True)
        except Exception as e:
            st.error(f"Lỗi xử lý ảnh: {e}")

with col_truth:
    st.subheader("✅ Đáp án (tùy chọn)")
    st.caption("Upload file JSON đáp án để chấm điểm tự động. Bỏ qua nếu chỉ muốn xem JSON cạnh nhau.")
    truth_file = st.file_uploader("Ground truth JSON", type=["json"], key="truth")
    truth_items = None
    if truth_file:
        try:
            truth_items = normalize_items(json.load(truth_file))
            st.success(f"Đáp án có {len(truth_items)} món")
        except Exception as e:
            st.error(f"JSON đáp án lỗi: {e}")

with st.expander("📝 Prompt (chỉnh được)"):
    prompt = st.text_area("Prompt xuất JSON", value=DEFAULT_PROMPT, height=220)
    if pipeline.startswith("Tách"):
        ocr_prompt = st.text_area("Prompt OCR (bước 1)", value=OCR_ONLY_PROMPT, height=120)
    else:
        ocr_prompt = OCR_ONLY_PROMPT

if "history" not in st.session_state:
    st.session_state["history"] = []   # mỗi phần tử: dict 1 lần chạy

run = st.button("🚀 Chạy model này", type="primary", use_container_width=True,
                disabled=(img_b64 is None))

if run and img_b64:
    if pipeline.startswith("VL gộp"):
        if not chosen_vl:
            st.warning("Chưa có model VL để chạy.")
        else:
            with st.spinner(f"Đang chạy {chosen_vl}..."):
                txt, sec, err = call_generate(server, chosen_vl, prompt,
                                              images=[img_b64], force_json=force_json)
            st.session_state["history"].append({
                "label": chosen_vl, "pipeline": "VL gộp",
                "text": txt, "sec": sec, "err": err,
                "ocr_text": None, "time": datetime.now().strftime("%H:%M:%S"),
            })

    else:  # tách 2 bước: VL OCR -> text model xuất JSON
        if not chosen_vl or not chosen_txt:
            st.warning("Cần chọn cả model VL (OCR) và model text (xuất JSON).")
        else:
            with st.spinner(f"Bước 1 — OCR bằng {chosen_vl}..."):
                ocr_text, ocr_sec, ocr_err = call_generate(
                    server, chosen_vl, ocr_prompt, images=[img_b64], force_json=False)
            if ocr_err:
                st.error(f"Lỗi OCR: {ocr_err}")
            else:
                full_prompt = f"{prompt}\n\n--- VĂN BẢN MENU ĐÃ ĐỌC ---\n{ocr_text}"
                with st.spinner(f"Bước 2 — {chosen_txt} xuất JSON..."):
                    txt, sec, err = call_generate(server, chosen_txt, full_prompt,
                                                  images=None, force_json=force_json)
                st.session_state["history"].append({
                    "label": f"{chosen_vl} → {chosen_txt}", "pipeline": "Tách 2 bước",
                    "text": txt, "sec": sec + ocr_sec, "err": err,
                    "ocr_text": ocr_text, "time": datetime.now().strftime("%H:%M:%S"),
                })

# ---------------- Hiển thị: kết quả mới nhất + so sánh tích lũy -------------
history = st.session_state["history"]

if history:
    # 1) Kết quả lần chạy mới nhất (chi tiết)
    st.divider()
    last = history[-1]
    st.subheader(f"📋 Kết quả mới nhất — {last['label']}")
    if last["err"]:
        st.error(f"Lỗi: {last['err']}")
    else:
        st.caption(f"⏱️ {last['sec']:.1f}s · {last['pipeline']} · {last['time']}")
        if last["ocr_text"]:
            with st.expander("📄 Văn bản OCR (bước 1)", expanded=False):
                st.text(last["ocr_text"])
        parsed = try_parse_json(last["text"])
        pred_items = normalize_items(parsed)
        if parsed is not None:
            st.success(f"✅ JSON hợp lệ — {len(pred_items) if pred_items else 0} món")
            if truth_items:
                sc = score_against_truth(pred_items, truth_items)
                c1, c2 = st.columns(2)
                c1.metric("Khớp tên", f"{sc['name_recall']}%", f"{sc['name_match']}/{sc['n_truth']}")
                c2.metric("Đúng giá", f"{sc['price_acc']}%", f"{sc['price_match']}/{sc['n_truth']}")
                if sc["missing"]:
                    st.caption("❌ Thiếu: " + ", ".join(sc["missing"][:15]))
                if sc["extra"]:
                    st.caption("➕ Dư: " + ", ".join(sc["extra"][:15]))
            st.json(parsed, expanded=False)
        else:
            st.warning("⚠️ JSON không parse được — xem raw:")
            st.text(last["text"][:3000])

    # 2) Bảng so sánh tích lũy tất cả các lần đã chạy
    if len(history) > 1:
        st.divider()
        st.subheader("📊 So sánh tất cả các lần chạy")
    summary = []
    for h in history:
        parsed = try_parse_json(h["text"])
        pred_items = normalize_items(parsed)
        row = {"Lúc": h["time"], "Model / Pipeline": h["label"],
               "Thời gian (s)": round(h["sec"], 1),
               "JSON hợp lệ": "✅" if (parsed is not None and not h["err"]) else "❌",
               "Số món": len(pred_items) if pred_items else 0}
        if truth_items and pred_items is not None:
            sc = score_against_truth(pred_items, truth_items)
            row["Khớp tên %"] = sc["name_recall"]
            row["Đúng giá %"] = sc["price_acc"]
        summary.append(row)
    st.dataframe(summary, use_container_width=True)
    st.caption("Mỗi lần chạy một model được lưu lại đây. Đổi model ở thanh bên rồi bấm "
               "“Chạy model này” để thêm vào bảng so sánh. Bấm “Xóa lịch sử so sánh” để làm lại.")

st.divider()
st.caption("💡 Mẹo: chạy qwen2.5vl:32b (VL gộp) trước. Nếu JSON chưa chuẩn, đổi sang "
           "tách 2 bước cho qwen2.5:32b xuất JSON từ text OCR. Upload đáp án JSON để chấm điểm tự động.")
