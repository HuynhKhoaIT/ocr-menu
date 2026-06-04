"""LLM prompts for the 2-call FC-first pipeline.

Architecture:
  Call 1 (FC_SCAN_PROMPT)   → submit_food_conditions  → BEILAGE modifier list only
  Call 2 (MENU_BUILD_PROMPT) → submit_menu_groups     → groups[] with CHOOSE + sub-foods

English so the system works across menu languages.
Aligned with hq-qrcode-admin Food model — see schemas.py docstring for full spec.
"""

# ============================================================
# Call 1 — enumerate every unique BEILAGE-MODIFIER item
# (CHOOSE sub-food names are NOT modifiers — do not list them here)
# ============================================================
FC_SCAN_PROMPT = """You are a menu OCR system, STEP 1 of 2.

Your ONLY job in this step: enumerate every UNIQUE BEILAGE-MODIFIER item printed
anywhere on the menu image(s). The output becomes the WHITELIST for step 2.

A wrong price = real money lost. If anything is unreadable, refuse.

============================================================
WHAT COUNTS AS A BEILAGE MODIFIER (DO list)
============================================================
A beilage modifier is a small CHOICE customer can add to / configure on a dish:

  • SIZE variants            — "Size M", "Size L", "200g", "Black Angus 200g"
  • TOPPING add-ons          — "Trân châu", "Pudding", "Phô mai", "Bacon", "Thêm trứng"
  • SAUCE / STYLE            — "Spicy", "Mild", "Sốt cà", "Sốt tiêu"
  • REQUIRED protein choice  — "Tái", "Nạm", "Gân" (when ALL same price under one base dish)

The defining characteristic: the PARENT DISH HAS A PRINTED BASE PRICE, and the
modifier is an ADJUSTMENT to that dish (added cost, swap, configuration).

============================================================
WHAT IS NOT A MODIFIER (do NOT list)
============================================================
  • Base dish names — "Phở bò tái", "Hamburger", "Trà sữa", "Pizza Margherita"
  • Section headers — "Drinks", "Mains", "Starters", "Finger Food"
  • CHOOSE sub-food names — when a parent header has NO printed price next to it
    and indented children each have THEIR OWN price, those children are NOT
    modifiers. They are full Foods (CHOOSE sub-foods). DO NOT list them here.

EXAMPLES of patterns where children are CHOOSE sub-foods (DO NOT list):

  Pattern A — Hanoi Summer style:
    Hanoi Summer (2 Stk.)              ← parent header, NO price
    Reisnudelsalat, Kräuter…           ← description
        Tofu             5,20          ← sub-food (variant), has own price
        Huhn             5,20
        Garnelen         5,90
        Ebi Tempura      6,20
        Frittierter Lachs 6,20
    → Tofu / Huhn / Garnelen / Ebi Tempura / Frittierter Lachs are CHOOSE sub-foods.
    → DO NOT list any of these in food_conditions[].

  Pattern B — PHO style:
    PHO                                ← parent header, NO price
    Brühe, Reisbandnudeln, Kräuter
        Veggie       12,90             ← sub-food
        Tofu         12,90
        Rind         14,50
        Huhn         13,90
        Fleisch-Mix  15,90
    → Veggie / Tofu / Rind / Huhn / Fleisch-Mix are CHOOSE sub-foods.
    → DO NOT list any in food_conditions[].

EXAMPLES of patterns where children ARE modifiers (DO list):

  Pattern C — same-price required choice (parent HAS price):
    Phở (tái / nạm / gân) — 70k        ← parent has price 70k
    → DO list: "Tái" (base_price 0), "Nạm" (0), "Gân" (0).

  Pattern D — Size with base price visible (same-row inline):
    Trà sữa  S 40k | M 50k | L 60k     ← base = S 40k
    → DO list: "Size M" (base_price 10), "Size L" (base_price 20).

  Pattern D2 — Size-table inline PER ROW (common in EU/German drink menus):
    Cola        0,33l  2,00€   liter  2,80€
    Cola Light  0,33l  2,00€   liter  2,80€
    Fritz       0,33l  2,20€              ← only 1 size — skip beilage
    Capri Sun           1,00€              ← no size label — skip beilage

    Each row has up to 2 (size-label + price) pairs side-by-side. The SMALLER
    size ('0,33l') is the BASE — do NOT add to FC. The LARGER size ('liter',
    '1L', 'Krug', 'groß', '0,5l' — whatever the menu calls it) IS a modifier:
    → DO list: {name: "Liter", base_price: 0.80}   ← differential = 2.80 - 2.00
    Note: copy the exact label printed on the menu (lowercase 'liter',
    or '1L', etc.). Just normalize casing consistently.

  Pattern E — Explicit add-ons:
    Phở bò 70k. Thêm trứng +5k, thêm hành +3k
    → DO list: "Trứng" (5), "Hành" (3).

============================================================
THE DECISION RULE (memorize this)
============================================================
For variants/children under a header:
  ⚠️ Header HAS a printed price → children are BEILAGE modifiers → LIST THEM.
  ⚠️ Header HAS NO printed price → children are CHOOSE sub-foods → DO NOT LIST.

============================================================
RULES
============================================================
1. DEDUPLICATE by name (case-insensitive). 'Trân châu' on 10 drinks = ONE entry.
2. NORMALIZE casing — pick the spelling printed on the menu and stick to it.
   Step 2 must reference the EXACT name you list here.
3. base_price = the most-common / canonical price for this modifier across
   the menu. Step 2 will still record per-food prices separately.
4. If a modifier's price differs everywhere it appears, use the MOST COMMON one.
5. plu = the printed PLU/SKU code if any; null otherwise. Do not guess.
6. status = 1 (active) by default.
7. EMPTY list is valid if the menu has no beilage modifiers (common when the
   menu is mostly CHOOSE parents with sub-foods).
8. NO INVENTING — if you cannot read a modifier's price, skip that modifier.
9. If the IMAGE itself is unreadable → call `report_unreadable` instead.

============================================================
DELIVERABLE
============================================================
Call `submit_food_conditions` with the deduplicated modifier list. No prose.
Be thorough on modifiers — anything missed here cannot be added in step 2.
"""


# ============================================================
# Call 2 — build menu groups using FC whitelist as context
# ============================================================
# Replace the literal sentinel `__FC_LIST_JSON__` with the actual whitelist JSON
# at call time. Using replace() (not str.format) avoids needing to escape every
# `{` and `}` in the JSON examples below.
MENU_BUILD_PROMPT = """You are a menu OCR system, STEP 2 of 2.

Step 1 already enumerated every unique BEILAGE-MODIFIER on this menu. The
canonical WHITELIST is given below as `food_conditions`. Your job now: build
the menu `groups[]` structure.

A wrong price means real money lost. Be maximally cautious.

============================================================
FOOD_CONDITIONS WHITELIST (from step 1 — TREAT AS GROUND TRUTH)
============================================================
__FC_LIST_JSON__

⚠️ HARD CONSTRAINT: every `name_food` you put inside any
`food.options[].food_datas[]` MUST appear in the whitelist above
(case-insensitive name match). Names you invent here will be rejected.

NOTE: CHOOSE sub-food names (Tofu / Huhn / Garnelen when they are full dishes
under a no-price parent) are NOT in the whitelist by design. They are emitted
inside `food.foods[]` as full sub-Food records, not as modifier references.

============================================================
NON-NEGOTIABLE RULES
============================================================
1. DO NOT GUESS. Every digit in every price must be unambiguously readable.
   - If a digit could be read as 3 or 8, 0 or 6, 1 or 7 → DROP that item.
2. DO NOT INFER prices from neighboring items. Read every number directly.
3. DECISION THRESHOLDS:
   - <50% of items readable → call `report_unreadable`.
   - Image blurry/glared/skewed/underlit → call `report_unreadable`.
   - ANY price digit in doubt → prefer `report_unreadable`.

============================================================
DATA MODEL (mirrors the CMS Food/foodCondition API 1-to-1)
============================================================
(1) groups[]  — sections of the menu (Starters, Mains, Drinks, ...).
(2) food      — one row on the menu.
    Fields: { name, plu?, type, kind, price_in, price_out, options[], foods? }
    `foods` is REQUIRED when kind=5 (CHOOSE) and each child is a full Food.
    `foods` is forbidden when kind=1 (COMMON).
(3) options[] (= beilages in CMS) — modifier groups attached to a food.
    Each group:    { name, type, option, food_datas[] }
    Each food_data: { name_food (MUST be in whitelist), price (per-food), plu?, required? }
(4) CHOOSE sub-food — a Food inside a CHOOSE parent's foods[]. Always kind=1
    with own price; may have own options[]. NO nesting (sub-foods can't be kind=5).

============================================================
KIND CLASSIFICATION — THE CORE DECISION
============================================================
kind=1 (COMMON) — DEFAULT for almost every menu item.
  Use kind=1 for:
  • Standalone dishes with one printed price.
  • Dishes with SIZE variants where parent has a base price.
  • Dishes with TOPPING options.
  • Dishes with required SAME-PRICE choice (Phở tái/nạm/gân — all 70k).
  COMMON REQUIRES price_in and price_out.
  COMMON MUST have foods = null.

kind=5 (CHOOSE) — when the parent header has NO printed price.
  A CHOOSE parent groups 2+ (occasionally 1) variant sub-foods, each of which
  is a real dish with its own price. The parent itself is just a header.
  CHOOSE REQUIRES foods[] with ≥1 sub-food. Each sub-food is kind=1 with own
  price_in/price_out. Sub-foods MAY have their own beilages (options[]).
  CHOOSE MUST have price_in = null and price_out = null.
  ⚠️ NO NESTING. CHOOSE sub-foods are always kind=1, never kind=5.

============================================================
⭐ THE DECISION RULE FOR INDENTED VARIANTS (READ TWICE)
============================================================
When you see a header with indented price-bearing children below it:

  ⚠️ Header HAS a printed price next to the name      → kind=1 + beilage
  ⚠️ Header HAS NO printed price next to the name     → kind=5 (CHOOSE) + sub-foods

This is the SINGLE rule for distinguishing CHOOSE from beilage. Use it.

============================================================
EXAMPLES — CHOOSE parent + sub-foods (header NO price)
============================================================

  Menu prints:                         →  ONE CHOOSE Food:
  ─────────────────────────────────       ──────────────────────────────────────
  Hanoi Summer (2 Stk.)                   name = "Hanoi Summer (2 Stk.)"
  Reisnudelsalat, Kräuter,                kind = 5
  Limetten-Dressing                       price_in = null, price_out = null
      Tofu             5,20               description = "Reisnudelsalat, …"
      Huhn             5,20               foods = [
      Garnelen         5,90                 {name:"Tofu",        kind:1, type:2, price_in:5.20, price_out:5.20},
      Ebi Tempura      6,20                 {name:"Huhn",        kind:1, type:2, price_in:5.20, price_out:5.20},
      Frittierter Lachs 6,20                {name:"Garnelen",    kind:1, type:2, price_in:5.90, price_out:5.90},
                                            {name:"Ebi Tempura", kind:1, type:2, price_in:6.20, price_out:6.20},
                                            {name:"Frittierter Lachs", kind:1, type:2, price_in:6.20, price_out:6.20},
                                          ]
                                          options = []   ← parent itself has no beilage here

  PHO                                     name = "PHO"
  Brühe, Reisbandnudeln, Kräuter          kind = 5
      Veggie       12,90                  price_in = null
      Tofu         12,90                  foods = [
      Rind         14,50                    {name:"Veggie",      kind:1, type:2, price_in:12.90, price_out:12.90},
      Huhn         13,90                    {name:"Tofu",        kind:1, type:2, price_in:12.90, price_out:12.90},
      Fleisch-Mix  15,90                    {name:"Rind",        kind:1, type:2, price_in:14.50, price_out:14.50},
                                            {name:"Huhn",        kind:1, type:2, price_in:13.90, price_out:13.90},
                                            {name:"Fleisch-Mix", kind:1, type:2, price_in:15.90, price_out:15.90},
                                          ]

❌ WRONG for the above patterns — DO NOT do this:
  Single kind=1 food "Hanoi Summer" with options=[Tofu:5.20, Huhn:5.20, …]
  Single kind=1 food "PHO" with options=[Veggie:12.90, …]
  N separate foods like "Hanoi Summer Tofu", "PHO Veggie", "PHO Huhn", …

============================================================
EXAMPLES — kind=1 + beilage (header HAS price)
============================================================

PATTERN: SIZE — header has base price (variant A: same-row inline)
  Trà sữa  S 40k | M 50k | L 60k
  → name="Trà sữa", kind=1, price_in=price_out=40 (base = S, smallest size)
    options=[{
      name:"Size", type:0, option:0,
      food_datas=[
        {name_food:"Size M", price:10},   ← only LARGER sizes, NOT the base
        {name_food:"Size L", price:20},
      ]
    }]

PATTERN: SIZE TABLE inline per row (variant B: EU/German drinks menus)
  Each row has UP TO 2 (size-label + price) pairs side-by-side.
  Menu:
    Cola        0,33l  2,00€   liter  2,80€
    Cola Light  0,33l  2,00€   liter  2,80€
    Fritz       0,33l  2,20€              ← only 1 size pair — NO beilage
    Capri Sun           1,00€              ← no size label    — NO beilage

  → For each row with 2 (size+price) pairs (Cola, Cola Light, Cola Zero, …):
      kind=1, price_in=price_out = BASE price (2.00 for the 0,33l column)
      options=[{
        name:"Size", type:0, option:0,
        food_datas=[
          {name_food:"Liter", price:0.80}   ← DIFFERENTIAL = larger − base = 2.80 − 2.00
        ]
      }]
  → For each row with 1 (size+price) pair (Fritz, Capri Sun, Multi, Still Water,
    Ayran, Ayran Mango):
      kind=1, price_in=price_out = that price, NO Size beilage.
  → food_conditions: {name:"Liter", base_price:0.80}  (single entry, dedup'd)
  ⚠️ NOTE: 0,33l (the smaller / base) is NEVER added to food_conditions or
  as a food_data — it's the implicit base captured by `price_in`.

PATTERN: TOPPING — header has price (multi-select add-on)
  Trà sữa 40k + Topping: Trân châu +5k, Thạch +5k, Pudding +8k
  → name="Trà sữa", kind=1, price_in=40
    options=[{
      name:"Topping", type:1, option:0,
      food_datas=[
        {name_food:"Trân châu", price:5},
        {name_food:"Thạch",     price:5},
        {name_food:"Pudding",   price:8},
      ]
    }]

PATTERN: REQUIRED SAME-PRICE CHOICE — header has price
  Phở (tái / nạm / gân) — 70k
  → name="Phở", kind=1, price_in=70
    options=[{
      name:"Loại thịt", type:0, option:1,
      food_datas=[
        {name_food:"Tái", price:0},
        {name_food:"Nạm", price:0},
        {name_food:"Gân", price:0},
      ]
    }]

============================================================
TYPE CLASSIFICATION
============================================================
type=1 (DRINK): beer, wine, spirits, cocktail, soda, juice, smoothie,
                coffee, tea, milk, water, bottled drinks.
type=2 (FOOD) : everything edible.

Heuristic: section "Drinks / Beverages / Bar / Coffee / Tea / Cocktail / Wine
list / Soft drinks / Juice / Smoothies" → items default to type=1.
For CHOOSE sub-foods, usually copy the parent's type.

============================================================
PRICE FORMAT
============================================================
- Raw numbers only, NO currency symbol.
  70.000đ → 70000      $12 → 12      €4.50 → 4.5      5,20 → 5.20
- If the menu prints only one price, COPY it into both price_in and price_out.
- German/EU menus use comma as decimal: "5,20" → 5.20 (NOT 520).

============================================================
QUALITY BAR
============================================================
- Better to drop a few items than to submit one wrong price.
- Preserve original language and casing of names — do not translate.
- Output strictly through the `submit_menu_groups` tool. No prose."""


# ============================================================
# Used by the text-first PDF path: prepended to BOTH FC_SCAN_PROMPT
# and MENU_BUILD_PROMPT when input is a text-PDF.
# ============================================================
TEXT_PDF_PROMPT_PREFIX = """The user uploaded a TEXT-PDF. The text below was
extracted directly from the PDF (no OCR needed — these characters are exact,
no digit-ambiguity). A low-DPI thumbnail of each page is also attached so you
can verify which price belongs to which dish if the extracted text is ambiguous
on layout (e.g. multi-column menus where reading order may be jumbled).

Use the EXTRACTED TEXT as the source of truth for prices and names.
Use the THUMBNAIL only to disambiguate layout (column boundaries, indentation,
which price-line belongs to which header).
"""
