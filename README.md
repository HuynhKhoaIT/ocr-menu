# Menu → JSON POS

OCR menu nhà hàng bằng LLM (Claude / GPT-4 vision) → trả về JSON đúng schema của CMS [hq-qrcode-admin](../hq-qrcode-admin) để **tạo thẳng Menu (group) và Food** qua API.

Mục tiêu cuối: chụp / upload 1 ảnh menu → đi qua pipeline OCR + LLM → có sẵn Menu + Food trong DB mà không cần nhập tay.

---

## 🚀 Quick start

```bash
# 1. Cài deps
pip install -r requirements.txt

# 2. Set API key (chọn 1 trong 2, hoặc cả 2)
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
echo "OPENAI_API_KEY=sk-proj-..."   >> .env

# 3. Chạy
streamlit run menu_ocr/app.py
# hoặc giữ command cũ:
streamlit run menu_compare_claude.py
```

Sau đó upload 1 ảnh menu (PNG/JPG/WebP/PDF) ở UI Streamlit và bấm **🚀 Chạy model này**.

---

## 🧱 Cấu trúc thư mục

```
ocr-benchmark/
├── menu_compare_claude.py        ← shim 3 dòng (entry point cũ)
├── requirements.txt
├── README.md                     ← bạn đang đọc
│
└── menu_ocr/                     ← package chính, 13 module
    ├── __init__.py
    │
    ├── config.py                 hằng số: model list, enum type/kind, blur thresholds
    ├── pricing.py                bảng giá $/1M token + compute_cost()
    ├── prompts.py                DEFAULT_PROMPT + OCR_ONLY_PROMPT (English)
    ├── schemas.py                JSON schema cho tool-use + OPENAI_TOOLS wrapper
    ├── models.py                 Pydantic FoodVariant / Food / Group / MenuExtraction
    ├── validation.py             validate_menu, flatten_foods, count_menu
    ├── image_utils.py            PDF→PNG, normalize, blur_score (Laplacian)
    ├── scoring.py                so sánh kết quả với ground-truth (tùy chọn)
    ├── clients_claude.py         Anthropic SDK wrapper (tool + text call)
    ├── clients_openai.py         OpenAI SDK wrapper (tool + text call)
    ├── clients_dispatch.py       chọn provider theo model id
    └── app.py                    Streamlit UI (main())
```

Mỗi file là 1 đơn vị nhỏ, ít phụ thuộc, dễ test/đổi riêng — vd. đổi schema chỉ sửa `schemas.py`, đổi tone prompt chỉ sửa `prompts.py`.

---

## 🔁 Luồng xử lý

```
┌─────────────┐
│ Upload      │  PNG / JPG / WebP / PDF
│ ảnh menu    │
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│ image_utils.py      │  normalize 1600px + Laplacian blur score
│ - PDF → PNG          │  (chặn cứng nếu mờ nặng)
│ - resize + RGB       │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ clients_dispatch.py │  chọn Claude hoặc OpenAI theo model id
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ LLM call            │  tool_use forced — bắt buộc gọi 1 trong 2 tool:
│ (Claude / OpenAI)   │   • submit_menu      → JSON đúng schema
│  prompts.py +       │   • report_unreadable → trả lý do, không bịa
│  schemas.py         │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ validation.py       │  Pydantic post-check:
│ validate_menu()     │   • type ∈ {1,2}, kind ∈ {1,5}
│                     │   • kind=1 (COMMON) phải có price_in/price_out
│                     │   • kind=5 (CHOOSE) phải có ≥2 variants
│                     │  Food fail bị drop, item ok vẫn giữ.
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ app.py UI           │   • Metric: groups / foods / variants / errors
│                     │   • Detail view: cây group → food → variant
│                     │   • Raw + validated JSON export
│                     │   • Optional: chấm điểm với ground-truth JSON
└─────────────────────┘
```

### 2 chế độ pipeline (chọn ở sidebar)

| Chế độ | Khi nào dùng |
|---|---|
| **Gộp** (1 bước) | Mặc định. Ảnh → thẳng JSON. Nhanh, ít token. |
| **Tách 2 bước** | Model rẻ (Haiku/4o-mini) làm OCR sang text → model mạnh đọc text xuất JSON. Khi muốn so sánh chất lượng OCR với chất lượng structuring riêng. |

---

## 📐 Schema output

LLM **bắt buộc** trả qua tool `submit_menu`. Cấu trúc 3 cấp:

```
food_conditions[]          ← danh sách modifier reusable (POST /foodcondition/create)
groups[]
  └── foods[]
        ├── beilages[]     ← nhóm modifier gắn lên food, items[] ref tới food_conditions
        └── combo_items[]  ← chỉ khi kind=5 (combo bundle)
```

### Ví dụ đầy đủ

```jsonc
{
  "food_conditions": [
    // Mỗi modifier name xuất hiện 1 lần. Price = canonical (mapper sẽ POST /foodcondition/create với giá này).
    { "name": "200g",              "price": 1.30 },
    { "name": "Black Angus 200g",  "price": 2.80 },
    { "name": "Trân châu",         "price": 5    },
    { "name": "Thạch",             "price": 5    }
  ],
  "groups": [
    {
      "name": "BURGER",
      "foods": [
        {
          "name": "Hamburger",
          "type": 2, "kind": 1,
          "price_in": 3.70, "price_out": 3.70,    // = cột 100g (size nhỏ nhất)
          "beilages": [
            {
              "group_name": "Size",
              "type": 0, "option": 0,             // single_choice, optional
              "food_datas": [
                // INLINE price (snapshot per food) — không phải ref string
                { "name_food": "200g",             "price": 1.30, "plu": null },
                { "name_food": "Black Angus 200g", "price": 2.80, "plu": null }
              ]
            }
          ],
          "combo_items": null
        },
        {
          "name": "BBQ Burger",
          "type": 2, "kind": 1,
          "price_in": 4.80, "price_out": 4.80,
          "beilages": [
            {
              "group_name": "Size",
              "type": 0, "option": 0,
              "food_datas": [
                { "name_food": "200g",             "price": 1.30, "plu": null },
                { "name_food": "Black Angus 200g", "price": 3.00, "plu": null }   // ← khác Hamburger!
              ]
            }
          ],
          "combo_items": null
        }
      ]
    },
    {
      "name": "Combos",
      "foods": [
        {
          "name": "Combo 2 người",
          "type": 2, "kind": 5,
          "price_in": null, "price_out": null,
          "combo_items": [
            { "name": "Gà rán", "price_in": 80,  "price_out": 80  },
            { "name": "Burger", "price_in": 100, "price_out": 100 },
            { "name": "Coca",   "price_in": 30,  "price_out": 30  }
          ],
          "beilages": []
        }
      ]
    }
  ]
}
```

> 🔑 **Tại sao inline price trong beilage**: cùng modifier "Black Angus 200g" có thể +2.80€ trên Hamburger nhưng +3.00€ trên BBQ Burger. Mỗi food cần snapshot price riêng. `food_conditions[].price` chỉ là giá canonical cho mapper khi tạo FoodCondition entity 1 lần.

### Enum number trùng với CMS

Để FE/BE consume trực tiếp không cần mapper convert:

| Field | Value | Label | Nguồn CMS |
|---|---|---|---|
| `type` | 1 | DRINK | `GoodsTypes.DRINK` |
| `type` | 2 | FOOD | `GoodsTypes.FOOD` |
| `kind` | 1 | COMMON | `GoodsKinds.COMMON` |
| `kind` | 5 | CHOOSE | `GoodsKinds.CHOOSE` |

> ⚠️ `kind = 5` (không phải 2) cho CHOOSE — đây là giá trị CMS đang dùng (`source/src/constants/index.js:206-211`), không phải typo.

### Quy tắc phân loại type / kind (đã bake vào prompt)

- **type=1 (DRINK)** nếu group/section là "Drinks / Beverages / Bar / Coffee / Tea / Cocktail / Wine list / Soft drinks / Juice / Smoothies" → mọi món bên trong mặc định DRINK.
- **type=2 (FOOD)** mặc định cho mọi thứ còn lại.
- **kind=1 (COMMON)** = **mặc định cho HẦU HẾT mọi món**, kể cả món có size variants, topping, add-on. Các option đó đi vào `beilages[]`, KHÔNG phải `combo_items`.
- **kind=5 (CHOOSE)** = **CHỈ combo bundle thật** — 1 lần mua giao 2+ món **khác nhau** (vd "Combo 2 người: gà + burger + coca"). Combo có `combo_items` ≥2 và `price_in/out = null`.

### Khi nào dùng `beilages` vs `combo_items`

| Tình huống menu | kind | beilages | combo_items |
|---|---|---|---|
| Phở 70k (1 món, 1 giá) | 1 | `[]` | `null` |
| Trà sữa S 40 / M 50 / L 60 | 1 | `[{group_name:"Size", items:["Size M","Size L"]}]` | `null` |
| Phở (tái/nạm/gân) cùng giá 70k | 1 | `[{group_name:"Loại thịt", type:0, option:1, items:[...]}]` | `null` |
| Trà sữa + 3 topping option | 1 | `[{group_name:"Topping", type:1, option:0, items:[...]}]` | `null` |
| Combo 2 người: gà + burger + coca = 250k | 5 | `[]` | 3 items |
| Combo có size cho nước trong combo | 5 | `[{group_name:"Size"}]` | combo items |

### Beilage extraction rules (đã bake vào prompt)

| Tình huống | price trong food_conditions | beilage.type / option |
|---|---|---|
| Size S 40 / M 50 / L 60 | `"Size M": 10`, `"Size L": 20` (= chênh lệch vs size nhỏ nhất) | type=0 single, option=0 optional |
| Topping +5k mỗi loại | `"Trân châu": 5`, `"Thạch": 5` | type=1 multi, option=0 optional |
| Phải chọn 1 vị (vd ngọt/cay/mặn) cùng giá | `"Ngọt": 0`, `"Cay": 0`, `"Mặn": 0` | type=0 single, option=1 required |
| Add-on "+5k thêm trứng" | `"Trứng": 5` | type=1 multi, option=0 optional |

### Combo pricing rule

| Tình huống combo | LLM phải làm |
|---|---|
| Combo in **giá riêng từng món** ("gà 80k + burger 100k + coca 30k = 210k") | combo_items giữ nguyên giá: 80 / 100 / 30 |
| Combo chỉ in **giá tổng** ("Combo 250k" với 3 món) | **Chia đều**: combo_items = 83.33 mỗi cái |

### Food_conditions dedup

- LLM tự gom tất cả modifier unique vào `food_conditions[]` top-level.
- Cùng `"Trân châu"` xuất hiện ở 10 món → chỉ 1 entry trong food_conditions, mỗi món `beilages[].items` chỉ ghi tên.
- Validator sẽ **drop food** nếu `beilages.items` reference tới name không có trong food_conditions (orphan).

---

## ⚙️ Configuration

### Biến môi trường

| Var | Bắt buộc | Mục đích |
|---|---|---|
| `ANTHROPIC_API_KEY` | Khi dùng Claude | API key Anthropic |
| `OPENAI_API_KEY` | Khi dùng OpenAI | API key OpenAI |
| `APP_PASSWORD` | Optional | Bật password gate để deploy public, tránh burn key |

Cả 3 đều có thể đặt trong `.env` (auto load bằng `python-dotenv`) hoặc nhập tay ở sidebar.

### Tuning ở sidebar

| Option | Default | Khi nào đổi |
|---|---|---|
| `max_tokens (JSON output)` | 4096 | Tăng nếu menu dài > 50 món bị cắt |
| `max_tokens (OCR step)` | 2048 | Chỉ ảnh hưởng pipeline tách 2 bước |
| `Cạnh dài ảnh tối đa (px)` | 1600 | Tăng 2000+ nếu chữ menu rất nhỏ; lưu ý tăng token cost |
| `Số trang PDF tối đa` | 5 | Tăng nếu menu nhiều trang |
| `Prompt caching` | ✓ | Tắt khi debug để thấy raw cost; bật khi production |
| `Cảnh báo ảnh mờ` | ✓ | Tắt khi test schema/prompt; bật khi user thật upload |

### Ngưỡng blur (Laplacian variance, đo trên ảnh đã normalize)

| Range | Hành vi |
|---|---|
| `> 300` | OK, chạy bình thường |
| `100 – 300` | ⚠️ Warning vàng, user phải tick xác nhận mới chạy |
| `< 100` | 🚫 Block cứng, không cho gọi API |

Sửa ngưỡng trong [menu_ocr/config.py](menu_ocr/config.py).

---

## 🔌 Tích hợp CMS (đang thiết kế)

Output của `validate_menu()` được build sao cho mapper có thể POST sang 3 API CMS theo **đúng thứ tự** dưới đây (vì food.beilages cần ref tới id của food_condition đã tạo):

```
1) POST /v1/foodcondition/create  ← mỗi `food_conditions[i]`
   → response.id  → build map { name → id }

2) POST /v1/group_food/create     ← mỗi `groups[i]`
   → response.id  → groupFoodId cho bước 3

3) POST /v1/food/create           ← mỗi `groups[i].foods[j]`
   Trong payload:
     - `groupFoodId` = id từ bước 2
     - `beilages[].food_datas[]` = resolve từ `beilages[].items` (lookup map ở bước 1)
     - Nếu kind=5: tạo parent (no price) → tạo mỗi combo_item với `parentId` của parent
```

Mapper FE/BE cần làm thêm:

1. **Nhân `price` với `0.01`** (CMS lưu coefficient ×100).
2. **Gói price**: `{qr_code:{in_price, out_price}, pickup, deliver}` → `JSON.stringify`.
3. **Resolve beilage refs**: với mỗi `food.beilages[].items[name]` → tìm id trong map → build food_data `{id, name_food, price, plu, productInfo}`.
4. **Auto-gen `plu`** nếu null (`OCR-<uuid8>`).
5. **Default fields**: `status=1`, `settings='{}'`, `happyHoursSetting='{}'`, `restaurantId=...`, `imagePath=null`.

Tham chiếu code/API CMS gốc:
- Group form: [GroupFoodForm.js](../hq-qrcode-admin/source/src/module-tenant/menu/GroupFoodForm.js)
- Food form : [FoodForm.js](../hq-qrcode-admin/source/src/module-tenant/menu/food/FoodForm.js)
- Beilage form : [BeilageForm.js](../hq-qrcode-admin/source/src/module-tenant/menu/food/BeilageForm.js)
- API config : [apiConfig.js:796-918](../hq-qrcode-admin/source/src/constants/apiConfig.js#L796-L918)
- Ingredient modal (đọc foodcondition): [IngredientModal.js](../hq-qrcode-admin/source/src/module-tenant/menu/food/IngredientModal.js)

> Phase tiếp theo: build FastAPI service nhận ảnh → trả JSON đã validated → FE / BE chạy orchestrator 3 bước trên. **Chưa làm**, đang chờ schema/prompt ổn định ở Streamlit này trước.

---

## 🎯 Anti-hallucination

LLM được ép qua **tool_choice = "any"** (Claude) / **`tool_choice="required"`** (OpenAI) — bắt buộc gọi 1 trong 2 tool:

| Tool | Khi nào model gọi |
|---|---|
| `submit_menu` | Khi đọc rõ ràng, đủ tự tin |
| `report_unreadable` | Khi mờ / nghi ngờ / < 50% món đọc rõ → trả `reason` + `suggestion` |

Prompt cũng nói rõ:
- Không suy diễn giá từ món lân cận.
- Mỗi chữ số phải đọc rõ — nghi ngờ thì bỏ món.
- Thiếu vài món còn hơn sai giá 1 món.

→ Trường hợp tệ nhất: model từ chối + bảo user chụp lại, không bao giờ tự bịa số.

---

## 🧪 Ground-truth scoring (optional)

Upload 1 JSON đáp án ở cột bên phải để chấm điểm tự động. Hỗ trợ 2 format:

**Format mới (nested)** — cùng schema với output:
```json
{"groups":[{"name":"...","foods":[{"name":"...","price_in":...}]}]}
```

**Format cũ (flat list)** — backward compat:
```json
[{"name":"Pho tái","price":70000}, ...]
```

Hệ thống flatten cả 2 phía rồi match theo `name` (lowercased), tính:
- **Name recall %**: % món có tên match
- **Price accuracy %**: % món match tên VÀ đúng giá (sai số < 0.01)

CHOOSE parent được expand thành nhiều dòng dạng `"<parent> - <variant>"` nên có thể so trực tiếp với menu phẳng.

---

## 💵 Cost tracking

Mỗi lần chạy hiển thị:
- Token in / out / cache_read / cache_write
- USD ước tính theo bảng giá `PRICING_PER_MTOK` trong [pricing.py](menu_ocr/pricing.py)
- Tổng $ session + average $ / run

Bảng giá hard-code — cập nhật khi Anthropic/OpenAI thay đổi giá.

---

## 🛠 Troubleshooting

| Triệu chứng | Nguyên nhân / Fix |
|---|---|
| `404 model not found` | Account chưa được grant model đó. Bấm 🔄 Reload models ở sidebar để lấy list thực. |
| Không có API key | Nhập ở sidebar hoặc set trong `.env`. |
| Streamlit chạy lại 2 lần | Bình thường — Streamlit rerun mỗi lần widget thay đổi state. |
| Pydantic báo nhiều lỗi `kind=5 requires variants` | LLM đoán nhầm kind. Sửa prompt hoặc giảm temperature (hiện không expose). |
| Cảnh báo spell-check Vietnamese | Comment/UI vẫn tiếng Việt; prompt đã English. Vô hại. |
| OOM khi đọc PDF dày | Giảm "Số trang PDF tối đa" ở sidebar. |

---

## 🗺 Roadmap

- [x] **Phase 1** — Schema + prompt + Streamlit benchmark (chính là repo này)
- [x] **Phase 1.5** — Refactor monolith → package `menu_ocr/`
- [ ] **Phase 2** — FastAPI service: `POST /ocr/menu` → trả JSON validated
- [ ] **Phase 3** — Mapper layer: build payload đúng format `POST /group_food/create` + `POST /food/create` (gồm coefficient ×0.01, default fields, PLU auto-gen)
- [ ] **Phase 4** — Orchestrator: loop create group → create food → create variants với `parentId` đúng, skip + report khi item lỗi
- [ ] **Phase 5** — Integration FE: nút "Import từ ảnh" trên `MenuListPage` của hq-qrcode-admin
- [ ] **Phase 6** — Undo last import (track ids đã tạo, bulk delete)

---

## 📦 Dependencies

Xem [requirements.txt](requirements.txt). Tóm tắt:
- `anthropic` / `openai` — SDK gọi LLM
- `streamlit` — UI
- `pydantic` — validate output
- `pymupdf` — render PDF
- `pillow` — xử lý ảnh
- `opencv-python-headless` + `numpy` — đo blur (Laplacian variance)
- `python-dotenv` — load `.env`

---

## 📄 License

Internal tool. Không public.
