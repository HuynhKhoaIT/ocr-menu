"""Map validated menu_ocr output to the exact CMS create payloads.

Three responsibilities:
- Apply the ×0.01 price coefficient the CMS expects.
- Build the JSON-stringified `price` / `settings` / `happyHoursSetting` fields.
- Resolve beilage food_data names to the FoodCondition ids returned from step 1.
- Auto-generate PLU when the menu does not print one.
"""
import json
import uuid


STATUS_ACTIVE = 1
PRICE_COEF = 0.01            # CMS stores prices ×100, payload must be ×0.01.
DEFAULT_TIME_OFFS = [0] * 7  # no off days by default
EMPTY_HAPPY_HOURS = {"qrcode_in": [], "qrcode_out": [], "pickup": [], "deliver": []}


def _gen_plu(prefix: str = "OCR") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _to_coef(v):
    """Apply the CMS price coefficient. None / missing → 0.

    Round to 4 decimals to avoid float-noise (1.30 * 0.01 = 0.013000000000000001
    in IEEE 754) leaking into the JSON payload the CMS receives.
    """
    return round((v or 0) * PRICE_COEF, 4)


def _price_json_for_common(price_in: float, price_out: float, sale_off: float = 0) -> str:
    return json.dumps({
        "qr_code": {
            "in_price":             price_in,
            "in_sale_off_percent":  sale_off,
            "out_price":            price_out,
            "out_sale_off_percent": sale_off,
        },
        "pickup":  {"price": price_in, "sale_off_percent": 0},
        "deliver": {"price": price_in, "sale_off_percent": 0},
    })


def _price_json_zero() -> str:
    return _price_json_for_common(0, 0)


# ============================================================
# FoodCondition (POST /v1/foodcondition/create)
# ============================================================
def map_food_condition(fc: dict, restaurant_id: int) -> dict:
    return {
        "foodConditionName":  fc["name"],
        "foodConditionPrice": _to_coef(fc.get("price")),
        "plu":                fc.get("plu") or _gen_plu("FC"),
        "productInfo":        fc.get("product_info") or "",
        "status":             STATUS_ACTIVE,
        "restaurantId":       restaurant_id,
    }


# ============================================================
# Group_food (POST /v1/group_food/create)
# ============================================================
def map_group_food(group: dict) -> dict:
    return {
        "name":         group["name"],
        "description":  group.get("description") or "",
        "imagePath":    None,
        "groupSetting": "{}",
        "status":       STATUS_ACTIVE,
    }


# ============================================================
# Food (POST /v1/food/create) — handles both kind=1 and kind=5
# ============================================================
def map_food(food: dict, ctx: dict, fc_id_map: dict) -> dict:
    """ctx = {groupFoodId, restaurantId, parentId?}
    fc_id_map = {name_lower: foodcondition_id} from the FC create phase.
    """
    if food["kind"] == 1:
        price_in  = _to_coef(food.get("price_in"))
        price_out = _to_coef(food.get("price_out"))
        price_json = _price_json_for_common(price_in, price_out, food.get("sale_off_percent") or 0)
    else:
        # kind=5 CHOOSE → parent carries no price (combo total lives on components)
        price_json = _price_json_zero()

    beilages_payload = []
    for bei in food.get("beilages") or []:
        food_datas = []
        for fd in bei.get("food_datas") or []:
            fc_id = fc_id_map.get((fd.get("name_food") or "").lower())
            if not fc_id:
                # Should never happen — validate_menu rejected orphans already.
                continue
            food_datas.append({
                "id":          fc_id,
                "name_food":   fd["name_food"],
                "price":       _to_coef(fd.get("price")),
                "plu":         fd.get("plu") or "",
                "productInfo": "",
            })
        if food_datas:
            beilages_payload.append({
                "group_name": bei["group_name"],
                "type":       bei["type"],
                "option":     bei["option"],
                "food_datas": food_datas,
            })

    return {
        "name":              food["name"],
        "description":       food.get("description") or "",
        "plu":               food.get("plu") or _gen_plu(),
        "productInfo":       food.get("product_info") or "",
        "type":              food["type"],
        "kind":              food["kind"],
        "status":            STATUS_ACTIVE,
        "groupFoodId":       ctx["groupFoodId"],
        "restaurantId":      ctx["restaurantId"],
        "parentId":          ctx.get("parentId"),
        "imagePath":         None,
        "systemPosPlu":      "",
        "timeFrom":          "",
        "timeEnd":           "",
        "settings":          json.dumps({"timeOffs": DEFAULT_TIME_OFFS}),
        "happyHoursSetting": json.dumps(EMPTY_HAPPY_HOURS),
        "beilages":          beilages_payload,
        "price":             price_json,
    }


# ============================================================
# Combo sub-food (POST /v1/food/create) — for components of a kind=5 parent
# ============================================================
def map_combo_item(item: dict, ctx: dict) -> dict:
    """ctx = {groupFoodId, restaurantId, parentId, parentType}"""
    price_in  = _to_coef(item.get("price_in"))
    price_out = _to_coef(item.get("price_out"))
    return {
        "name":              item["name"],
        "description":       "",
        "plu":               item.get("plu") or _gen_plu(),
        "productInfo":       "",
        "type":              ctx.get("parentType", 2),
        "kind":              1,   # combo components are always COMMON
        "status":            STATUS_ACTIVE,
        "groupFoodId":       ctx["groupFoodId"],
        "restaurantId":      ctx["restaurantId"],
        "parentId":          ctx["parentId"],
        "imagePath":         None,
        "systemPosPlu":      "",
        "timeFrom":          "",
        "timeEnd":           "",
        "settings":          "{}",
        "happyHoursSetting": json.dumps(EMPTY_HAPPY_HOURS),
        "beilages":          [],
        "price":             _price_json_for_common(price_in, price_out),
    }
