"""OpenAI Vision OCR for menus, using Structured Outputs.

Usage:
    python menu_scan_openai.py <image-path>
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")


class MenuItem(BaseModel):
    category: str = Field(
        description=(
            "Tên danh mục/nhóm món chứa món này, đúng như in trên menu "
            "(vd: 'Khai vị', 'Món chính', 'Đồ uống', 'Appetizers'). "
            "Nếu menu không có danh mục rõ ràng, để chuỗi rỗng ''. "
            "KHÔNG tự nghĩ ra danh mục."
        )
    )
    name: str = Field(
        description=(
            "Tên món y nguyên 100% như in trên menu. "
            "KHÔNG sửa chính tả, KHÔNG dịch, KHÔNG viết tắt, KHÔNG bổ sung từ. "
            "Giữ đầy đủ dấu tiếng Việt."
        )
    )
    priceText: str | None = Field(
        description=(
            "BƯỚC 1 (PHẢI LÀM TRƯỚC price): Chép Y NGUYÊN chuỗi giá bạn NHÌN THẤY "
            "trên ảnh, bao gồm dấu chấm, dấu phẩy, ký hiệu tiền tệ, đơn vị "
            "(vd: '50.000đ', '50k', '$5.00', '120').\n"
            "QUY TẮC GROUNDING (chống bịa):\n"
            "  - Chỉ chép những ký tự BẠN THỰC SỰ NHÌN THẤY RÕ trên ảnh.\n"
            "  - Với mỗi chữ số/ký tự MỜ, BỊ CHE, KHÔNG ĐỌC ĐƯỢC → thay bằng '?'. "
            "VD: thấy '5_.000đ' nhưng chữ số thứ hai mờ → ghi '5?.000đ'. "
            "Thấy '___' không đọc được gì → ghi '???'.\n"
            "  - Nếu KHÔNG NHÌN THẤY giá nào ở dòng món này → null.\n"
            "  - TUYỆT ĐỐI KHÔNG điền số mà bạn không thực sự nhìn thấy trên ảnh. "
            "Đây là phép kiểm chứng nhận thức — nếu bịa ở đây sẽ bị phát hiện."
        )
    )
    price: float | None = Field(
        description=(
            "BƯỚC 2 (chỉ làm SAU khi đã điền priceText): chuyển priceText thành số.\n\n"
            "RÀNG BUỘC TUYỆT ĐỐI:\n"
            "  - Nếu priceText = null → price = null.\n"
            "  - Nếu priceText chứa BẤT KỲ ký tự '?' nào → price = null. "
            "KHÔNG được đoán phần thiếu.\n"
            "  - Chỉ tính số khi priceText rõ ràng 100%, không có '?'.\n\n"
            "QUY ƯỚC CHUYỂN ĐỔI VND:\n"
            "  - '50.000đ' / '50.000 VND' / '50.000' → 50000\n"
            "  - '50k' / '50K' → 50000\n"
            "  - Số nguyên đứng một mình không có ký hiệu tiền tệ và không có dấu chấm "
            "ngăn cách hàng nghìn (vd '5', '15', '20', '25', '50', '120', '250') → "
            "NHÂN 1000. VD: '5' → 5000, '15' → 15000, '120' → 120000, '250' → 250000.\n"
            "QUY ƯỚC NGOẠI TỆ (có ký hiệu $ / € / £ hoặc số thập phân .00):\n"
            "  - '$5' / '$5.00' → 5.00\n"
            "  - '4,50€' → 4.50\n"
            "  - KHÔNG nhân 1000."
        )
    )


class MenuExtraction(BaseModel):
    items: list[MenuItem]


SYSTEM_PROMPT = (
    "Bạn là trợ lý OCR menu nhà hàng. Nhiệm vụ DUY NHẤT: trích xuất CHÍNH XÁC những "
    "món ăn và giá đang in trên ảnh menu.\n\n"
    "QUY TRÌNH BẮT BUỘC cho mỗi món (theo đúng thứ tự):\n"
    "  Bước 1 — category, name: chép tên danh mục và tên món y nguyên trên ảnh.\n"
    "  Bước 2 — priceText: NHÌN vào vùng giá của món này, chép lại Y NGUYÊN "
    "những ký tự BẠN THỰC SỰ THẤY. Mọi chữ số mờ/không rõ → đánh dấu '?'. "
    "Đây là phép kiểm chứng nhận thức.\n"
    "  Bước 3 — price: CHỈ tính số từ priceText. Nếu priceText có '?' hoặc null "
    "→ price BẮT BUỘC là null. Không được 'sửa lại' để đoán giá.\n\n"
    "QUY TẮC TUYỆT ĐỐI — vi phạm là sai:\n"
    "1. CHỈ trích xuất món bạn ĐỌC RÕ TÊN trên ảnh. Tên mờ → bỏ qua hoàn toàn.\n"
    "2. KHÔNG dùng kiến thức nền về món ăn để đoán giá. KHÔNG ước lượng theo "
    "món tương tự hoặc mặt bằng giá nhà hàng.\n"
    "3. KHÔNG dịch tên món. KHÔNG sửa chính tả. Chép y nguyên.\n"
    "4. Mỗi món trích xuất đúng 1 lần.\n"
    "5. BỎ QUA: mô tả món, thành phần, ghi chú, header, footer, số trang, "
    "tagline quảng cáo, địa chỉ, số điện thoại, giờ mở cửa.\n\n"
    "VÍ DỤ:\n"
    "  - Thấy rõ 'Phở bò ... 50.000đ' → "
    '{category:"", name:"Phở bò", priceText:"50.000đ", price:50000}\n'
    "  - Thấy 'Bún chả ... 6_.000đ' (chữ số thứ hai mờ) → "
    '{category:"", name:"Bún chả", priceText:"6?.000đ", price:null}\n'
    "  - Thấy 'Trà đá ... [vết ố không đọc được]' → "
    '{category:"", name:"Trà đá", priceText:"???", price:null}\n'
    "  - Thấy 'Cơm tấm ... 35' (số đứng một mình trong menu VN) → "
    '{category:"", name:"Cơm tấm", priceText:"35", price:35000}\n\n'
    "Nguyên tắc cốt lõi: thà null còn hơn sai. Người dùng cần phát hiện chỗ ảnh "
    "mờ để chụp lại, không cần bạn đoán hộ."
)


def _data_url(image_path: str) -> str:
    mime, _ = mimetypes.guess_type(image_path)
    if not mime:
        mime = "image/jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def scan_with_openai(image_path: str) -> dict:
    client = OpenAI()
    data_url = _data_url(image_path)

    started = time.time()
    response = client.chat.completions.parse(
        model=MODEL,
        response_format=MenuExtraction,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Trích xuất các món và giá từ ảnh menu này."},
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            },
        ],
    )
    elapsed_ms = int((time.time() - started) * 1000)

    message = response.choices[0].message
    if message.refusal:
        raise RuntimeError(f"Model refused: {message.refusal}")

    extraction: MenuExtraction = message.parsed
    items = [item.model_dump() for item in extraction.items] if extraction else []

    nullified = 0
    for it in items:
        pt = it.get("priceText")
        if it.get("price") is not None and (
            pt is None or "?" in pt or pt.strip() == ""
        ):
            it["price"] = None
            it["nullifiedReason"] = "ungrounded: priceText missing or contains '?'"
            nullified += 1

    usage = response.usage
    return {
        "image": str(Path(image_path).resolve()),
        "model": MODEL,
        "elapsedMs": elapsed_ms,
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }
        if usage
        else None,
        "itemCount": len(items),
        "nullifiedCount": nullified,
        "items": items,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python menu_scan_openai.py <image-path>", file=sys.stderr)
        sys.exit(1)
    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"File not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    result = scan_with_openai(image_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
