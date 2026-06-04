"""JSON schemas for tool-use and the Anthropic/OpenAI tool wrappers.

Mirrors the CMS create-food / create-group / create-foodcondition payloads
1-to-1. See hq-qrcode-admin Food model:
  https://github.com/.../module-tenant/menu/food/

Data model overview:
- FoodCondition  = reusable modifier item (e.g. "Size L", "Trân châu", "Tofu" as
                   topping) — shared across menus. Each FoodCondition has its
                   own base_price.
- FoodData       = one row inside an OptionGroup (beilage) with its own per-food
                   price + `required` flag, references FoodCondition by name.
- OptionGroup    = a modifier GROUP attached to a Food (e.g. "Size", "Topping",
                   "Loại thịt"). Single- or multi-select; required or optional.
- Food kind=1 (COMMON) = standalone dish with one base price + optional
                   beilages (size/topping/required-choice modifiers).
- Food kind=5 (CHOOSE) = parent header that groups variant sub-foods.
                   - Parent itself has NO price.
                   - Children are FULL Foods (each kind=1, own price, own PLU,
                     can have own beilages).
                   - In the CMS this is stored as separate Food records linked
                     by `parentId`; the OCR emits them inline as `foods[]` for
                     clarity, the API mapper flattens.
                   - NOT a "combo bundle". A CHOOSE parent's sub-foods are
                     mutually-exclusive variants of the same dish concept
                     (e.g. "Hanoi Summer" with Tofu / Huhn / Garnelen variants).
"""
from menu_ocr.config import FOOD_TYPES, FOOD_KINDS


# ============================================================
# Reusable FoodCondition — base record dedup'd at the top level
# ============================================================
FOOD_CONDITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "base_price"],
    "properties": {
        "plu": {
            "type": ["string", "null"],
            "description": "PLU/SKU code if printed on the menu. NULL otherwise — do not guess.",
        },
        "name": {
            "type": "string",
            "description": (
                "Unique modifier name (e.g. 'Size L', 'Trân châu', 'Tái', 'Phô mai'). "
                "Used as the KEY referenced from food.options[].food_datas[]. "
                "Must be unique across the menu (case-insensitive). "
                "DO NOT include names of CHOOSE sub-foods here — sub-foods are full "
                "Food records, not modifier items."
            ),
        },
        "base_price": {
            "type": "number",
            "minimum": 0,
            "description": (
                "Canonical/default price for this modifier (most-common price across "
                "occurrences). Per-food override lives in OptionGroup.food_datas[].price. "
                "0 is valid (e.g. 'no sugar', or required-choice same-price variants)."
            ),
        },
        "status": {
            "type": ["integer", "null"],
            "description": "Status flag. Default 1 (active). NULL if not printed.",
        },
    },
}


# ============================================================
# FoodData — one option inside an OptionGroup (with inline price)
# ============================================================
FOOD_DATA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name_food", "price"],
    "properties": {
        "name_food": {
            "type": "string",
            "description": (
                "Display name of this option (e.g. 'Size L', 'Trân châu', 'Tái'). "
                "MUST also exist (same spelling) in the top-level food_conditions[] array."
            ),
        },
        "plu": {
            "type": ["string", "null"],
            "description": "PLU/SKU if printed. NULL otherwise.",
        },
        "price": {
            "type": "number",
            "minimum": 0,
            "description": (
                "Actual ADDITIONAL price for THIS food. Same modifier can have DIFFERENT "
                "prices on different foods — that's why we store it inline. "
                "For size: price = difference vs smallest size (base). "
                "For free required choices (vd Tái/Nạm/Gân cùng giá): price = 0."
            ),
        },
        "required": {
            "type": ["boolean", "null"],
            "description": (
                "True if this specific option is pre-selected / auto-checked by default. "
                "False or null otherwise. Group-level 'must pick something' lives in "
                "OptionGroup.option, not here."
            ),
        },
        "status": {
            "type": ["integer", "null"],
            "description": "Status flag. Default 1 (active). NULL if not printed.",
        },
    },
}


# ============================================================
# OptionGroup — modifier GROUP attached to a Food (= 'beilage' in admin UI)
# ============================================================
OPTION_GROUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "option", "food_datas"],
    "properties": {
        "name": {
            "type": "string",
            "description": "Name of the modifier group, e.g. 'Size', 'Topping', 'Loại thịt'.",
        },
        "type": {
            "type": "integer",
            "enum": [0, 1],
            "description": (
                "Selection mode. "
                "0 = single_choice (customer picks EXACTLY ONE — e.g. Size, Loại thịt). "
                "1 = multi_choice (customer picks ZERO OR MORE — e.g. Toppings)."
            ),
        },
        "option": {
            "type": "integer",
            "enum": [0, 1],
            "description": (
                "Is choosing from this group required? "
                "0 = optional (customer may skip). "
                "1 = required (customer must pick at least one). "
                "Rule of thumb: size with smallest-as-default → option=0; "
                "size with no default → option=1; toppings → usually option=0; "
                "required protein/style choice (Phở tái/nạm/gân) → option=1."
            ),
        },
        "food_datas": {
            "type": "array",
            "minItems": 1,
            "description": (
                "Inline list of options in this group. Each entry has name + actual price. "
                "Names MUST also appear in the top-level food_conditions[] array."
            ),
            "items": FOOD_DATA_SCHEMA,
        },
    },
}


# ============================================================
# CHOOSE sub-food — Food that lives INSIDE a kind=5 CHOOSE parent.
# Locked to kind=1 (no nested CHOOSE), full pricing + optional own beilages.
# ============================================================
# Defined separately from FOOD_SCHEMA so we can structurally prevent
# CHOOSE-inside-CHOOSE: this schema has no `foods` field, and kind is fixed.
CHOOSE_SUB_FOOD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "kind", "price_in", "price_out"],
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "Sub-food name (the variant identity, e.g. 'Tofu', 'Huhn', 'Garnelen', "
                "'Frittierter Lachs'). Copy exactly from the image."
            ),
        },
        "plu": {"type": ["string", "null"], "description": "PLU if printed. NULL otherwise."},
        "type": {
            "type": "integer",
            "enum": FOOD_TYPES,
            "description": "1 = DRINK, 2 = FOOD. Usually same as the CHOOSE parent's type.",
        },
        "kind": {
            "type": "integer",
            "enum": [1],
            "description": "Always 1 (COMMON). CHOOSE parents cannot contain other CHOOSE parents.",
        },
        "price_in":  {"type": "number", "minimum": 0,
                      "description": "Dine-in price for this sub-food (printed on the menu)."},
        "price_out": {"type": "number", "minimum": 0,
                      "description": "Take-away price. Copy price_in if menu shows one price."},
        "options": {
            "type": "array",
            "description": (
                "Optional beilages attached to THIS sub-food (e.g. extra topping). "
                "Empty array if none."
            ),
            "items": OPTION_GROUP_SCHEMA,
        },
    },
}


# ============================================================
# Food — top-level menu row (standalone COMMON or CHOOSE parent)
# ============================================================
FOOD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "kind"],
    "properties": {
        "name": {"type": "string", "description": "Item name copied exactly from the image, original language preserved."},
        "plu":  {"type": ["string", "null"], "description": "PLU/SKU if printed (COMMON only). NULL otherwise."},
        "type": {
            "type": "integer",
            "enum": FOOD_TYPES,
            "description": (
                "1 = DRINK (soda, juice, tea, coffee, beer, cocktail, smoothie, water, milk...). "
                "2 = FOOD (everything edible). If the group/section is 'Drinks / Beverages / "
                "Bar / Coffee / Tea / Cocktail / Wine list / Soft drinks' → all items default "
                "to type=1. Otherwise default to type=2."
            ),
        },
        "kind": {
            "type": "integer",
            "enum": FOOD_KINDS,
            "description": (
                "1 = COMMON (default). One base price; size/topping/required-choice variants "
                "live in options[] (beilages). MUST have foods=null.\n"
                "5 = CHOOSE — a parent header that groups variant sub-foods, where the "
                "parent itself has NO printed price and each child variant has its OWN price. "
                "Example: 'Hanoi Summer' with sub-foods 'Tofu' 5.20, 'Huhn' 5.20, "
                "'Garnelen' 5.90, 'Ebi Tempura' 6.20, 'Frittierter Lachs' 6.20. "
                "kind=5 MUST have foods[]≥1 and price_in=price_out=null.\n"
                "DECISION RULE for indented variants: parent has a printed price → "
                "kind=1 + beilage. Parent has NO printed price → kind=5 + sub-foods."
            ),
        },
        "price_in": {
            "type": ["number", "null"],
            "minimum": 0,
            "description": (
                "Dine-in base price (smallest variant if size beilage exists). "
                "REQUIRED when kind=1. MUST be null when kind=5 (parent has no price)."
            ),
        },
        "price_out": {
            "type": ["number", "null"],
            "minimum": 0,
            "description": (
                "Take-away base price. Copy price_in if menu shows only one price. "
                "REQUIRED when kind=1. MUST be null when kind=5."
            ),
        },
        "sale_off_percent": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 100,
            "description": "Discount % if a struck-through original price is shown. NULL otherwise.",
        },
        "description":  {"type": ["string", "null"], "description": "Short note printed next to item."},
        "product_info": {"type": ["string", "null"], "description": "Ingredients / allergens if printed."},
        "options": {
            "type": "array",
            "description": (
                "Beilages (modifier groups) on this food. ALLOWED for both kind=1 and kind=5. "
                "Empty array if no modifiers."
            ),
            "items": OPTION_GROUP_SCHEMA,
        },
        "foods": {
            "type": ["array", "null"],
            "minItems": 1,
            "description": (
                "CHOOSE sub-foods — REQUIRED (≥1 entry, typically ≥2) when kind=5. "
                "MUST be null when kind=1. Each child is a full Food with kind=1, "
                "own price, optional own beilages. No nesting (sub-foods cannot be kind=5)."
            ),
            "items": CHOOSE_SUB_FOOD_SCHEMA,
        },
    },
}


# ============================================================
# Group — section on the menu
# ============================================================
GROUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "foods"],
    "properties": {
        "name":        {"type": "string", "description": "Section/category name (e.g. 'Appetizers', 'Drinks')."},
        "description": {"type": ["string", "null"], "description": "Section description if printed."},
        "foods": {
            "type": "array",
            "minItems": 1,
            "items": FOOD_SCHEMA,
        },
    },
}


# ============================================================
# Call 1 — submit_food_conditions (FC scan only)
# ============================================================
TOOL_SUBMIT_FOOD_CONDITIONS = {
    "name": "submit_food_conditions",
    "description": (
        "Step 1 of 2: Enumerate ALL unique BEILAGE-MODIFIER items printed on the menu.\n\n"
        "A modifier item is any small CHOICE the customer can add to / pick for a dish "
        "(size, topping, sauce, required protein/style choice).\n\n"
        "EXAMPLES of modifiers (DO include):\n"
        "  • Size variants    — 'Size M', 'Size L', '200g', 'Black Angus 200g'\n"
        "  • Topping add-ons  — 'Trân châu', 'Pudding', 'Phô mai', 'Bacon'\n"
        "  • Sauce / style    — 'Spicy', 'Mild', 'Sốt cà', 'Sốt tiêu'\n"
        "  • Required protein choice (same-price): 'Tái', 'Nạm', 'Gân' for Phở at 70k\n\n"
        "WHAT IS NOT A MODIFIER (do NOT include):\n"
        "  • Base dish names — 'Phở bò tái', 'Hamburger', 'Trà sữa', 'Pizza Margherita'\n"
        "  • Section headers — 'Drinks', 'Mains', 'Starters'\n"
        "  • CHOOSE sub-food NAMES — when a parent header has NO printed price and its "
        "indented children each have DIFFERENT prices (e.g. 'Hanoi Summer' with sub-foods "
        "Tofu 5.20, Huhn 5.20, Garnelen 5.90, Ebi Tempura 6.20). Those are NOT modifiers — "
        "they are CHOOSE sub-foods (full Food records). Step 2 will emit them as kind=5 "
        "parent + sub-foods. DO NOT list 'Tofu', 'Huhn', 'Garnelen' etc. here.\n\n"
        "RULES:\n"
        "1. DEDUPLICATE by name (case-insensitive). 'Trân châu' on 10 drinks = ONE entry.\n"
        "2. base_price = the most-common / canonical price you see for this modifier.\n"
        "3. Empty list IS valid if the menu has no beilage modifiers (rare).\n"
        "4. If image is too blurry / unreadable → call report_unreadable instead.\n"
        "5. EVERY name you list here will be the WHITELIST for step 2 — be thorough; "
        "missing modifiers in step 1 cannot be added back in step 2."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["food_conditions"],
        "properties": {
            "food_conditions": {
                "type": "array",
                "description": (
                    "Deduplicated list of every unique BEILAGE-MODIFIER name on the menu "
                    "(size/topping/sauce/required-choice). Does NOT include CHOOSE "
                    "sub-food dish names. This list becomes the whitelist for step 2."
                ),
                "items": FOOD_CONDITION_SCHEMA,
            },
        },
    },
}


# ============================================================
# Call 2 — submit_menu_groups (build groups using FC whitelist)
# ============================================================
TOOL_SUBMIT_MENU_GROUPS = {
    "name": "submit_menu_groups",
    "description": (
        "Step 2 of 2: Build the menu structure using the food_conditions WHITELIST "
        "from step 1. STRICT RULES:\n"
        "1. EVERY price digit must be unambiguously readable. If you cannot tell whether "
        "a digit is '3' or '8', '0' or '6', '1' or '7' — call report_unreadable.\n"
        "2. Wrong price = real money lost. Bias toward refusing.\n"
        "3. If less than 50% of items are readable, call report_unreadable.\n"
        "4. NEVER infer prices from neighboring items. Read every number directly.\n"
        "5. type: 1=DRINK, 2=FOOD. kind: 1=COMMON, 5=CHOOSE (parent for variant sub-foods).\n"
        "6. DECISION RULE for indented variants under a header:\n"
        "   - Header has a PRINTED PRICE → kind=1 + beilage (Size/Topping/Required choice)\n"
        "   - Header has NO printed price → kind=5 (CHOOSE) + sub-foods (each kind=1 with own price)\n"
        "7. kind=5 CHOOSE has foods[]≥1 children. Each child is kind=1 with own "
        "price_in/price_out — NO nesting (sub-foods cannot themselves be kind=5).\n"
        "8. EVERY name_food inside any food.options[].food_datas[] MUST match a name "
        "from the food_conditions whitelist (case-insensitive). Do NOT invent new modifier "
        "names here. (CHOOSE sub-food NAMES are NOT in the whitelist — they go in foods[].)\n"
        "9. Per-food price snapshots in food_datas[].price MAY differ from the whitelist's "
        "base_price (that's the point of the per-food override)."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["groups"],
        "properties": {
            "groups": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Each section/category on the menu becomes one group. "
                    "If the menu has no sections, return a single group named 'Menu'. "
                    "Skip any food whose name or price is not clearly readable."
                ),
                "items": GROUP_SCHEMA,
            },
        },
    },
}


# ============================================================
# Refusal tool (shared by both steps)
# ============================================================
TOOL_REPORT_UNREADABLE = {
    "name": "report_unreadable",
    "description": (
        "PREFERRED tool whenever there is ANY doubt about readability. "
        "Call this INSTEAD of submit_* when:\n"
        "- The image is blurry, dark, low-res, occluded, or skewed.\n"
        "- Any price digit is ambiguous.\n"
        "- More than a few items are hard to read.\n"
        "- You feel tempted to 'estimate' or 'infer' prices.\n"
        "Better to ask the user to retake the photo than to submit one wrong price."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["reason", "suggestion"],
        "properties": {
            "reason":     {"type": "string", "description": "Why the image cannot be read (English)."},
            "suggestion": {"type": "string", "description": "Concrete suggestion to the user on how to recapture (English)."},
        },
    },
}


# Tool sets for each call.
TOOLS_FC   = [TOOL_SUBMIT_FOOD_CONDITIONS, TOOL_REPORT_UNREADABLE]
TOOLS_MENU = [TOOL_SUBMIT_MENU_GROUPS,     TOOL_REPORT_UNREADABLE]


# ============================================================
# OpenAI tool wrappers
# ============================================================
def _to_openai(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name":        tool["name"],
            "description": tool["description"],
            "parameters":  tool["input_schema"],
        },
    }


OPENAI_TOOLS_FC   = [_to_openai(t) for t in TOOLS_FC]
OPENAI_TOOLS_MENU = [_to_openai(t) for t in TOOLS_MENU]
