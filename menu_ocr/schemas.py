"""JSON schemas for tool-use and the Anthropic/OpenAI tool wrappers.

Mirrors the CMS create-food / create-group / create-foodcondition payloads
1-to-1 — see MENU_SCHEMA.md for the full spec.

Data model overview:
- FoodCondition  = reusable modifier item (e.g. "Tofu", "Đường") with base_price
                   shared across menus.
- FoodData       = one row inside an OptionGroup with its own per-food price +
                   `required` flag, references FoodCondition by name/plu.
- OptionGroup    = a modifier GROUP attached to a Food (e.g. "Size", "Topping").
- Food kind=1    = standalone item with one base price + optional options[].
- Food kind=5    = combo bundle — has child `foods[]` (each child kind=1, no
                   further nesting allowed).
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
                "Unique modifier name (e.g. 'Trân châu', 'Size L', 'Tofu', 'Phô mai'). "
                "Used as the KEY referenced from food.options[].food_datas[]. "
                "Must be unique across the menu (case-insensitive). Use the EXACT same "
                "spelling whenever you reference this item from multiple foods."
            ),
        },
        "base_price": {
            "type": "number",
            "minimum": 0,
            "description": (
                "Canonical/default price for this modifier (most-common price across "
                "occurrences). Per-food override lives in OptionGroup.food_datas[].price. "
                "0 is valid (e.g. 'no sugar')."
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
                "Display name of this option (e.g. '200g', 'Tofu', 'Size L'). "
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
                "For free required choices (vd tái/nạm same price): price = 0."
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
# OptionGroup — modifier GROUP attached to a Food
# (was 'Beilage' in the previous schema)
# ============================================================
OPTION_GROUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "option", "food_datas"],
    "properties": {
        "name": {
            "type": "string",
            "description": "Name of the modifier group, e.g. 'Size', 'Topping', 'Protein'.",
        },
        "type": {
            "type": "integer",
            "enum": [0, 1],
            "description": (
                "Selection mode. "
                "0 = single_choice (customer picks EXACTLY ONE — e.g. Size). "
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
                "size with no default → option=1; toppings → usually option=0."
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
# Food LEAF — a Food that is INSIDE a combo (no further nesting allowed)
# ============================================================
# Defined separately from FOOD_SCHEMA so we can enforce "no combo-in-combo"
# structurally: leaf has no `foods` field at all, and kind is locked to 1.
FOOD_LEAF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "kind", "price_in", "price_out"],
    "properties": {
        "name": {"type": "string", "description": "Component item name copied verbatim."},
        "plu":  {"type": ["string", "null"], "description": "PLU if printed. NULL otherwise."},
        "type": {
            "type": "integer",
            "enum": FOOD_TYPES,
            "description": "1 = DRINK, 2 = FOOD. Determines POS category.",
        },
        "kind": {
            "type": "integer",
            "enum": [1],   # combo children are ALWAYS kind=1 — no nested combos
            "description": "Always 1. Combo children cannot themselves be combos.",
        },
        "price_in":  {"type": "number", "minimum": 0,
                      "description": "Dine-in price for this component."},
        "price_out": {"type": "number", "minimum": 0,
                      "description": "Take-away price. Copy price_in if menu shows one price."},
        "options": {
            "type": "array",
            "description": "OptionGroups (size/topping/etc) attached to this combo component. Empty if none.",
            "items": OPTION_GROUP_SCHEMA,
        },
    },
}


# ============================================================
# Food — top-level menu row (standalone item or combo container)
# ============================================================
FOOD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "type", "kind"],
    "properties": {
        "name": {"type": "string", "description": "Item name copied exactly from the image, original language preserved."},
        "plu":  {"type": ["string", "null"], "description": "PLU/SKU if printed. NULL otherwise."},
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
                "1 = COMMON (DEFAULT). One base price; size/topping variants live in options[]. "
                "5 = COMBO bundle — 2+ DIFFERENT items sold together (e.g. 'Combo 2 người: "
                "Phở + Coca = 250k'). Combos have child foods[] (kind=1 each, NO nesting). "
                "Size variants of the SAME dish are NEVER kind=5 — they are kind=1 + Size option."
            ),
        },
        "price_in": {
            "type": ["number", "null"],
            "minimum": 0,
            "description": (
                "Dine-in base price (smallest/cheapest variant if size exists). "
                "REQUIRED when kind=1. MUST be null when kind=5 (combo total is implicit "
                "from child foods)."
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
                "OptionGroups attached to this food. ALLOWED for both kind=1 and kind=5. "
                "Empty array if no modifiers."
            ),
            "items": OPTION_GROUP_SCHEMA,
        },
        "foods": {
            "type": ["array", "null"],
            "minItems": 2,
            "description": (
                "Combo components. REQUIRED (≥2 entries) when kind=5. MUST be null when kind=1. "
                "Each child is a complete Food with kind=1 (combos cannot nest)."
            ),
            "items": FOOD_LEAF_SCHEMA,
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
        "Step 1 of 2: Enumerate ALL unique modifier items printed on the menu.\n\n"
        "A modifier item is any choice the customer can pick to customize a dish:\n"
        "  • Size variants    — 'Size M', 'Size L', '200g', 'Black Angus 200g'\n"
        "  • Topping add-ons  — 'Trân châu', 'Pudding', 'Phô mai', 'Bacon'\n"
        "  • Protein swaps    — 'Tofu', 'Huhn', 'Lachs', 'Rind', 'Garnelen'\n"
        "  • Sauce/style      — 'Spicy', 'Mild', 'Sốt cà', 'Tái', 'Nạm'\n"
        "  • Drink choices in combos — 'Coca', 'Sprite', 'Trà đá'\n\n"
        "RULES:\n"
        "1. DEDUPLICATE by name (case-insensitive). 'Trân châu' on 10 drinks = ONE entry.\n"
        "2. base_price = the most-common / canonical price you see for this modifier "
        "across the whole menu. If price varies per dish, pick the most common.\n"
        "3. Do NOT include base dish names (Phở, Hamburger, Trà sữa) — those are foods, not modifiers.\n"
        "4. Empty list IS valid if the menu truly has no modifiers (rare).\n"
        "5. If image is too blurry / unreadable → call report_unreadable instead.\n"
        "6. EVERY name you list here will be the WHITELIST for step 2 — be thorough; "
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
                    "Deduplicated list of every unique modifier name seen on the menu. "
                    "This list becomes the canonical whitelist for step 2."
                ),
                "items": FOOD_CONDITION_SCHEMA,
            },
        },
    },
}


# ============================================================
# Call 2 — submit_menu_groups (build groups using FC whitelist)
# ============================================================
# Same shape as a Food but with an extra constraint baked into the prompt: every
# name_food inside any options[].food_datas[] MUST come from the FC whitelist
# provided by step 1. The schema itself cannot enforce that (JSON Schema has no
# cross-reference); the Pydantic validator does (orphan check).
TOOL_SUBMIT_MENU_GROUPS = {
    "name": "submit_menu_groups",
    "description": (
        "Step 2 of 2: Build the menu structure using the food_conditions WHITELIST "
        "provided in the system prompt. STRICT RULES:\n"
        "1. EVERY price digit must be unambiguously readable. If you cannot tell whether a digit is "
        "'3' or '8', '0' or '6', '1' or '7' — call report_unreadable.\n"
        "2. Wrong price = real money lost. Bias toward refusing.\n"
        "3. If less than 50% of items are readable, call report_unreadable.\n"
        "4. NEVER infer prices from neighboring items. Read every number directly.\n"
        "5. type: 1=DRINK, 2=FOOD. kind: 1=COMMON (default), 5=COMBO (bundle only).\n"
        "6. Size/topping variants → kind=1 + options[] (NOT kind=5).\n"
        "7. Combos (kind=5) have child foods[]. Each child kind=1 — NO combos inside combos.\n"
        "8. EVERY name_food inside any food.options[].food_datas[] MUST match a name "
        "from the food_conditions whitelist (case-insensitive). Do NOT invent new modifier "
        "names here — if you need one that's missing, that's a bug in step 1.\n"
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
