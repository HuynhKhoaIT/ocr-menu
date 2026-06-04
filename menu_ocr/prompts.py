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
WHAT IS A BEILAGE MODIFIER — conceptual definition
============================================================
A beilage modifier is any CONFIGURATION CHOICE the customer can make to
augment, adjust, or pick a variant OF a base dish. It is NOT itself a dish.

It earns the label "modifier" if ALL three hold:

  ✓ It modifies a PARENT DISH that has its own printed base price on the menu.
  ✓ It changes a feature / adds an extra / picks a variant — but the parent
    dish remains the dish (size swap, topping, sauce, required protein style).
  ✓ It does NOT stand alone on the menu as a separate priced item.

If even ONE fails — most importantly, if the parent has NO printed base price
and the variants each have their OWN price — those variants are NOT modifiers.
They are CHOOSE sub-foods (full Food records) and belong to step 2's foods[],
NOT this step's food_conditions[].

============================================================
⭐ THE SINGLE DECISION RULE (applies to ANY layout)
============================================================
For any indented / grouped variants under a header line:

  Parent has a PRINTED PRICE on the menu  → variants are BEILAGE modifiers → LIST
  Parent has NO PRINTED PRICE             → variants are CHOOSE sub-foods → SKIP

This rule alone resolves every layout you will encounter — table, inline,
indented, multi-column, columnar header, footnote. The pattern examples
below are illustrations of HOW the rule applies; they are not an exhaustive
checklist. When you see a layout not listed, apply the rule directly.

============================================================
CATEGORIES (semantic guidance — name and price differently per category)
============================================================
Once you've decided something IS a modifier (rule above), categorize it:

  • SIZE / PORTION         — adjusts quantity. Name = the variant label
                             ("Size M", "Size L", "200g", "1L", "Krug").
                             Price = DIFFERENTIAL from the parent's base.
  • TOPPING / ADD-ON       — optional extra item added on top of the dish.
                             Name = the topping ("Trân châu", "Phô mai",
                             "Trứng"). Price = the extra charge as printed.
  • SAUCE / STYLE / SPICE  — flavor/style config. Name = the style label
                             ("Spicy", "Mild", "Sốt cà"). Price = the extra
                             if any, else 0.
  • REQUIRED PROTEIN/STYLE PICK at same price — choice within a single dish
                             with one printed price. Name = the choice label
                             ("Tái", "Nạm", "Gân"). Price = 0 (no extra).

These are STARTING categories — if the menu uses a label that fits none
exactly (e.g. allergen tag, language variant, prep style), still emit it as
a modifier IF the decision rule above says it's a modifier. Use the closest
category name in your head; the field doesn't enforce a fixed set.

============================================================
EXAMPLES — illustrating the rule, not enumerating layouts
============================================================
GOOD (parent has price → list the modifiers):

  Phở (tái / nạm / gân) — 70k                  ← parent has price 70k
    → list: "Tái" (0), "Nạm" (0), "Gân" (0)    — same-price required pick

  Trà sữa  S 40k | M 50k | L 60k               ← parent has price (S as base)
    → list: "Size M" (10), "Size L" (20)        — size-up differential

  Cola        0,33l  2,00€   liter  2,80€      ← parent has price (small as base)
  Cola Light  0,33l  2,00€   liter  2,80€      → list ONCE: "Liter" (0.80)
  Fritz       0,33l  2,20€                      — Fritz has only 1 size, skip
  Capri Sun           1,00€                     — Capri Sun has no size, skip

  Phở bò 70k. Thêm trứng +5k, thêm hành +3k    ← explicit add-ons
    → list: "Trứng" (5), "Hành" (3)

BAD — these look like modifiers but ARE NOT (parent has no price):

  Hanoi Summer (2 Stk.)              ← NO price on parent line
      Tofu             5,20
      Huhn             5,20
      Garnelen         5,90
    → DO NOT list Tofu / Huhn / Garnelen. These are CHOOSE sub-foods.

  PHO                                ← NO price on parent line
      Veggie       12,90
      Rind         14,50
    → DO NOT list Veggie / Rind. These are CHOOSE sub-foods.

============================================================
WHEN YOU SEE AN UNFAMILIAR LAYOUT
============================================================
Do NOT try to pattern-match. Instead:

  Step 1. Find the parent line (the dish header).
  Step 2. Does the parent have a printed price on its own line?
            YES → any variant below it is a beilage modifier → LIST.
            NO  → variants are CHOOSE sub-foods → SKIP (step 2 handles them).
  Step 3. For modifiers you decide to list, pick the right price interpretation:
            - Differential if variant changes the parent (size, premium upgrade)
            - Absolute extra cost if variant is an additional item (topping)
            - 0 if variant is a same-price required pick (protein style)

Trust the rule. If uncertain after step 2, defaulting to NOT listing is safer
than over-listing — orphan refs from step 2 will be auto-filled as a safety net.

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
KIND CLASSIFICATION — conceptual definition
============================================================
kind=1 (COMMON) — the dish is the unit of purchase.
  The PARENT line is itself a complete priced dish. Any variants below it
  modify the dish (size, topping, sauce, required protein) but do not
  REPLACE the dish — the customer still orders "the dish" plus chosen
  modifiers. Use kind=1 whenever the parent has a printed base price.
  COMMON REQUIRES price_in and price_out. MUST have foods = null.

kind=5 (CHOOSE) — the dish is a category; the variants are the real dishes.
  The PARENT line is just a header / category label with NO printed price.
  Each child variant has its OWN price and IS the actual dish the customer
  orders. The header exists only to group the variants visually.
  CHOOSE REQUIRES foods[] ≥ 1. Each sub-food is kind=1 with own price.
  Sub-foods MAY have their own beilages. price_in / price_out = null.
  ⚠️ NO NESTING. CHOOSE sub-foods are always kind=1, never kind=5.

============================================================
⭐ THE SINGLE DECISION RULE (applies to ANY layout)
============================================================
When you see a header with grouped variants below it:

  Parent has a PRINTED PRICE on the menu  → kind=1 + beilage (options[])
  Parent has NO PRINTED PRICE             → kind=5 (CHOOSE) + sub-foods (foods[])

This one rule resolves every layout: indented children, multi-column tables,
inline same-row variants, footnote add-ons, language-specific patterns.
The examples below are illustrations of how the rule applies in common
layouts — they are NOT an exhaustive list. When you see a layout not shown
here, do not try to pattern-match it: apply the rule directly.

WHEN UNCERTAIN — fallback heuristic in priority order:
  1. Does the parent line have a price next to its name? → kind=1.
  2. Do the children have INDIVIDUAL prices, parent line has none? → kind=5.
  3. Are all children at the SAME price under a priced parent? → kind=1 +
     required beilage (type=0, option=1).
  4. Default to kind=1 if still unsure (a wrong kind=1 with one beilage is
     more recoverable than a wrong kind=5 that drops the parent's price).

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
