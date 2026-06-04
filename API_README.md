# Menu OCR — FastAPI Service

Production HTTP wrapper for the `menu_ocr` extraction pipeline + optional auto-import vào CMS POS.

---

## 🚀 Quick start

```bash
pip install -r requirements.txt

# .env
OPENAI_API_KEY=sk-proj-...
# ANTHROPIC_API_KEY=sk-ant-...    # optional, for Claude models
OCR_DEFAULT_MODEL=gpt-5.5
OCR_MAX_TOKENS=12000

# Optional: enable /import/menu (auto-create vào CMS)
CMS_BASE_URL=https://tenant-api-beta.digibes.de/
CMS_TOKEN=<bearer-token>
CMS_TENANT_ID=<x-tenant-header-value>
CMS_RESTAURANT_ID=<restaurant-id>

# Start
uvicorn menu_api.main:app --reload --port 8000
```

Swagger UI: <http://localhost:8000/docs>

---

## 🧱 Cấu trúc

```
menu_api/
├── __init__.py
├── settings.py        env config (.env loader, defaults)
├── main.py            FastAPI app — 4 endpoints
├── models.py          Pydantic request/response
├── pipeline.py        image → LLM → validated JSON  (wraps menu_ocr)
├── mapper.py          LLM JSON → CMS create payload (×0.01 + JSON.stringify + PLU gen)
├── cms_client.py      async httpx wrapper (Authorization Bearer + X-Tenant)
└── orchestrator.py    full 3-phase import + skip-and-report on failure
```

Phụ thuộc vào package `menu_ocr/` (LLM call, validators, prompts) — không duplicate logic.

---

## 🔌 Endpoints

### `GET /health`
Kiểm tra config — không gọi LLM hay CMS.

```bash
curl http://localhost:8000/health
```
```json
{
  "ok": true,
  "default_model": "gpt-5.5",
  "anthropic_key_set": false,
  "openai_key_set": true,
  "cms_configured": true,
  "cms_base_url": "https://tenant-api-beta.digibes.de/",
  "cms_tenant_id": "...",
  "cms_restaurant_id": "..."
}
```

---

### `GET /models`
Liệt kê model khả dụng từ Anthropic + OpenAI (theo key đã set).

```bash
curl http://localhost:8000/models
```

---

### `POST /ocr/menu`  — chỉ OCR, không push CMS
Trả về JSON đã validated theo schema `{food_conditions, groups}`.

**Form fields:**
| Field | Type | Default | Mô tả |
|---|---|---|---|
| `file` | file | required | PNG/JPG/WebP/PDF |
| `model` | string | env `OCR_DEFAULT_MODEL` | Override model |
| `max_tokens` | int | env `OCR_MAX_TOKENS` | Output cap |
| `skip_blur_check` | bool | false | Bỏ qua Laplacian sharpness |
| `parallel` | bool | false | PDF nhiều trang → fan-out 1 call/page, merge server-side. Giảm latency ~60% cho PDF 3+ trang. |

**Example:**
```bash
curl -X POST http://localhost:8000/ocr/menu \
  -F "file=@menu.jpg" \
  -F "max_tokens=12000"
```

**Response 200 (success):**
```json
{
  "status": "ok",
  "model": "gpt-5.5",
  "data": {
    "food_conditions": [
      {"name": "200g", "price": 1.30, "plu": null, "product_info": null}
    ],
    "groups": [
      {"name": "BURGER", "foods": [...]}
    ]
  },
  "errors": [],
  "counts": {
    "groups": 1, "foods": 5, "combos": 0, "combo_items": 0,
    "beilage_groups": 5, "beilage_links": 10, "food_conditions": 2
  },
  "truncated": false,
  "max_tokens_used": 12000,
  "sec": 18.4,
  "cost_usd": 0.0231,
  "usage": {"input_tokens": 2010, "output_tokens": 3900, "cache_read": 0, "cache_create": 1200},
  "blur_score": 412.5,
  "stop_reason": "tool_use",
  "n_pages": 1
}
```

**Response 200 (model refused):**
```json
{
  "status": "unreadable",
  "model": "gpt-5.5",
  "reason": "Image is severely blurred; price digits cannot be resolved.",
  "suggestion": "Retake the photo with stable hands and adequate lighting.",
  "sec": 12.1,
  "cost_usd": 0.0089,
  ...
}
```

---

### `POST /import/menu` — full pipeline (extract + create vào CMS)

Same form fields as `/ocr/menu` plus:
| Field | Default | Mô tả |
|---|---|---|
| `skip_cms` | false | Chỉ chạy OCR, không POST CMS |
| `restaurant_id` | env `CMS_RESTAURANT_ID` | Override cho request này |
| `parallel` | false | Áp cho phase OCR — fan-out per-page nếu PDF nhiều trang |

**Pipeline 3 phase:**
1. POST `/v1/foodcondition/create` × N (mỗi unique modifier) → build `{name→id}` map
2. POST `/v1/group_food/create` × M (mỗi group)
3. POST `/v1/food/create` × K (mỗi food + combo sub-foods, có ref tới id ở phase 1+2)

**Failure policy = SKIP + REPORT.** Bất kỳ item nào fail đều ghi vào `cms_report.failed[]`, pipeline KHÔNG abort. Group fail thì food trong group đó bị skip (vì thiếu groupFoodId), nhưng các group khác vẫn chạy.

**Example:**
```bash
curl -X POST http://localhost:8000/import/menu \
  -F "file=@menu.jpg" \
  -F "max_tokens=12000"
```

**Response 200:**
```json
{
  "ocr": { ...same shape as /ocr/menu... },
  "skipped_cms": false,
  "cms_report": {
    "food_conditions": {
      "created": [{"name": "200g", "id": "8925..."}],
      "failed":  []
    },
    "groups": {
      "created": [{"name": "BURGER", "id": "8924..."}],
      "failed":  []
    },
    "foods": {
      "created": [
        {"name": "Hamburger", "group": "BURGER", "id": "8923..."}
      ],
      "failed": []
    },
    "combo_items": {
      "created": [], "failed": []
    },
    "summary": {
      "food_conditions": {"ok": 2, "fail": 0},
      "groups":          {"ok": 1, "fail": 0},
      "foods":           {"ok": 5, "fail": 0},
      "combo_items":     {"ok": 0, "fail": 0}
    }
  }
}
```

**Response khi OCR refuse hoặc CMS chưa config:**
```json
{
  "ocr": { ... },
  "skipped_cms": true,
  "skip_reason": "OCR status=unreadable"  // hoặc "CMS env vars not configured"
}
```

---

## ⚡ Parallel per-page mode (cho PDF nhiều trang)

Khi PDF có **3+ trang**, thêm `parallel=true` để fan-out 1 LLM call mỗi page,
gather song song bằng `asyncio.gather`, rồi merge server-side.

### So sánh

| | Single-call (default) | `parallel=true` |
|---|---|---|
| Số call LLM | 1 (gửi N ảnh) | N (mỗi ảnh 1 call) |
| Wall-clock | Sum tất cả page | Max của các page (parallel) |
| Truncation risk | Cao khi N≥4 | Thấp (mỗi call output nhỏ) |
| Failure isolation | 1 fail → all fail | 1 page fail → others vẫn có |
| Cost | 1x | ~1.0-1.1x (cache giúp) |

### Curl
```bash
curl -X POST http://localhost:8000/ocr/menu \
  -F "file=@menu_5_pages.pdf" \
  -F "parallel=true" \
  -F "max_tokens=8000"
```

### Response thêm field
```jsonc
{
  "status": "ok",
  "mode": "parallel-per-page",      // ← khác "single-call"
  "data": { ... },                   // menu đã merge
  "page_results": [
    {"page": 1, "ok": true,  "tool": "submit_menu",       "sec": 18.2, "usage": {...}},
    {"page": 2, "ok": true,  "tool": "submit_menu",       "sec": 22.5, "usage": {...}},
    {"page": 3, "ok": false, "tool": "report_unreadable", "reason": "blurry"},
    {"page": 4, "ok": true,  ...},
    {"page": 5, "ok": false, "error": "API 429: ..."}
  ]
}
```

### Merge rules (đã implement)

| Trường hợp | Hành vi |
|---|---|
| Cùng `food_conditions[].name` ở nhiều page, **cùng price** | Giữ entry đầu, price = giá đó |
| Cùng `food_conditions[].name`, **khác price** giữa các page | Price = **0** (conflict signal) — per-food price vẫn lưu inline trong `beilage.food_datas[]` |
| Cùng `groups[].name` ở nhiều page | Merge `foods[]` arrays, giữ description dài hơn |
| 1 page fail (timeout/error/refuse) | Bỏ page đó, các page khác vẫn merge, ghi vào `page_results[].ok=false` |
| Tất cả page fail | Status = `unreadable` |

### Khi NÊN dùng `parallel=true`
- PDF ≥ 3 trang
- Menu dense (mỗi trang nhiều món) → tránh truncate
- Cần latency thấp cho UX

### Khi KHÔNG cần
- Single image hoặc PDF 1-2 trang (overhead không đáng)
- Menu cần dedup chéo trang phức tạp (LLM single-call hiểu context tốt hơn)

---

## ⚙️ Environment variables

| Var | Required? | Mặc định | Mô tả |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Khi dùng Claude | — | API key Anthropic |
| `OPENAI_API_KEY` | Khi dùng OpenAI | — | API key OpenAI |
| `OCR_DEFAULT_MODEL` | optional | `gpt-5.5` | Model dùng nếu request không override |
| `OCR_MAX_TOKENS` | optional | `12000` | Output token cap (menu >50 món nên ≥12000) |
| `OCR_MAX_PDF_PAGES` | optional | `5` | Số trang PDF tối đa |
| `OCR_MAX_IMAGE_SIDE` | optional | `1600` | Cạnh dài ảnh normalize |
| `CMS_BASE_URL` | cho /import | — | vd `https://tenant-api-beta.digibes.de/` |
| `CMS_TOKEN` | cho /import | — | Bearer access token |
| `CMS_TENANT_ID` | cho /import | — | Giá trị header `X-Tenant` |
| `CMS_RESTAURANT_ID` | cho /import | — | `restaurantId` trong payload |
| `CMS_TIMEOUT_SECONDS` | optional | `30` | Timeout httpx |
| `CORS_ORIGINS` | optional | `*` | Comma-separated origins, `*` = all |

Cả `/ocr/menu` và `/import/menu` đều CHẠY ĐƯỢC khi không có CMS config — `/import/menu` chỉ tự skip phase CMS và trả `skipped_cms=true`.

---

## 🧪 Test bằng Swagger

1. Mở http://localhost:8000/docs
2. Expand `POST /ocr/menu` → **Try it out**
3. Upload ảnh menu → **Execute**
4. Xem response để verify schema trước khi gọi `/import/menu`

---

## 🛠 Troubleshooting

| Triệu chứng | Nguyên nhân | Fix |
|---|---|---|
| `500 client init failed: ANTHROPIC_API_KEY missing` | Thiếu key | Set vào `.env` rồi restart |
| `500 OCR failed: API 404` | Model id sai/account chưa grant | `GET /models` để xem list thực |
| `truncated: true` + `data: {groups: []}` | max_tokens quá nhỏ | Tăng `max_tokens` form field hoặc `OCR_MAX_TOKENS` env |
| `status: unreadable` | Model refuse vì ảnh mờ/khó đọc | Chụp lại theo `suggestion`; hoặc set `skip_blur_check=true` (rủi ro) |
| `cms_report.foods.failed` toàn `transport error` | CMS URL sai hoặc network | Verify `CMS_BASE_URL` accessible từ host |
| `cms_report.foods.failed` toàn `HTTP 401` | Token hết hạn / sai | Refresh `CMS_TOKEN` |
| `cms_report.foods.failed` toàn `beilage references not found` | Validator bug (orphan check) | Không nên xảy ra; nếu có thì file issue |

---

## 🔗 Quan hệ với 2 entry point khác

- **Streamlit** ([menu_compare_claude.py](menu_compare_claude.py) / [menu_ocr/app.py](menu_ocr/app.py)): GUI để benchmark prompt/model + so sánh ground truth. Dùng khi tune chất lượng.
- **FastAPI** (file này): HTTP API cho production tự động. Dùng khi tích hợp vào FE/BE khác.
- **Core logic** ([menu_ocr/](menu_ocr/)): shared modules — schemas, prompts, validators, LLM clients. Cả 2 entry point đều import từ đây.

---

## 🗺 Roadmap

- [x] OCR endpoint (`/ocr/menu`)
- [x] Full import endpoint (`/import/menu`) — sequential 3-phase
- [x] Skip+report failure policy
- [ ] Bulk endpoint (multiple images cùng lúc, parallel)
- [ ] Undo endpoint (`/import/undo`) — delete by ids in cms_report
- [ ] Webhook callback khi import xong (async background task)
- [ ] Auth middleware cho service này (hiện đang trust mọi caller)
