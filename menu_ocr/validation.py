"""Validate raw LLM output + flatten/count helpers used by the UI and scoring."""
import copy
from typing import List

from menu_ocr.config import KIND_CHOOSE, KIND_COMMON
from menu_ocr.models import (
    ChooseSubFood,
    Food,
    FoodCondition,
    FoodData,
    Group,
    OptionGroup,
    PYDANTIC_OK,
    ValidationError,
)


def _err(path, name, message):
    return {"path": path, "name": str(name)[:60], "message": message}


def _autofill_food_conditions(raw_menu: dict) -> int:
    """Safety net for sloppy LLMs.

    Scan every food.options[].food_datas[] (and every combo-child food too)
    and append any name missing from the top-level food_conditions[] array.
    Auto-added entry uses the inline price from the first occurrence as
    base_price. Mutates raw_menu IN-PLACE.
    Returns the number of food_conditions added (0 if nothing was missing).
    """
    if not isinstance(raw_menu, dict):
        return 0
    fcs = raw_menu.setdefault("food_conditions", []) or []
    if not isinstance(fcs, list):
        return 0

    existing = {(fc.get("name") or "").strip().lower()
                for fc in fcs if isinstance(fc, dict)}

    added = 0

    def _scan_food(f: dict) -> None:
        nonlocal added
        if not isinstance(f, dict):
            return
        for og in f.get("options") or []:
            if not isinstance(og, dict):
                continue
            for fd in og.get("food_datas") or []:
                if not isinstance(fd, dict):
                    continue
                name = (fd.get("name_food") or "").strip()
                if not name:
                    continue
                key = name.lower()
                if key in existing:
                    continue
                fcs.append({
                    "name":       name,
                    "base_price": fd.get("price") or 0,
                    "plu":        fd.get("plu"),
                    "status":     1,
                })
                existing.add(key)
                added += 1
        # Also walk CHOOSE sub-foods — their options[] can reference modifiers too.
        for child in f.get("foods") or []:
            _scan_food(child)

    for g in raw_menu.get("groups") or []:
        if not isinstance(g, dict):
            continue
        for f in g.get("foods") or []:
            _scan_food(f)

    raw_menu["food_conditions"] = fcs
    return added


def merge_groups_payloads(payloads):
    """Merge multiple per-page submit_menu_groups payloads into one groups[].

    Each payload is the tool_input dict from one page: {'groups': [...]}.
    Groups with the same name (case-insensitive) across pages are FUSED:
    their foods[] arrays are concatenated, description from first occurrence.
    First-seen order is preserved.

    Returns: {'groups': [...]}.
    """
    by_name: dict = {}
    order: list = []
    for p in payloads or []:
        if not isinstance(p, dict):
            continue
        for g in p.get("groups") or []:
            if not isinstance(g, dict):
                continue
            name = (g.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key not in by_name:
                by_name[key] = {
                    "name":        name,
                    "description": g.get("description"),
                    "foods":       list(g.get("foods") or []),
                }
                order.append(key)
            else:
                by_name[key]["foods"].extend(g.get("foods") or [])
                if not by_name[key].get("description") and g.get("description"):
                    by_name[key]["description"] = g["description"]
    return {"groups": [by_name[k] for k in order]}


def merge_fc_and_groups(fc_list, groups_payload: dict) -> dict:
    """Combine Call-1 (food_conditions) and Call-2 (groups) results into the
    single {food_conditions, groups} shape that validate_menu expects.

    `fc_list` may be the raw list from submit_food_conditions tool_input
    (list of dicts), or already wrapped in {'food_conditions': [...]}.
    `groups_payload` is the tool_input from submit_menu_groups,
    expected to be {'groups': [...]}.
    """
    if isinstance(fc_list, dict) and "food_conditions" in fc_list:
        fcs = fc_list["food_conditions"] or []
    elif isinstance(fc_list, list):
        fcs = fc_list
    else:
        fcs = []
    groups = (groups_payload or {}).get("groups") or []
    return {"food_conditions": fcs, "groups": groups}


def validate_menu(raw_menu: dict):
    """Validate the LLM's submit_menu payload against the nested schema.

    Returns (validated_dict, errors). `errors` is a list of dicts:
        {"path": "groups[0].foods[3]", "name": "...", "message": "..."}
    Items that fail are dropped from the validated tree; valid items kept.

    Cross-cutting checks beyond the Pydantic models:
      * Every food.options[].food_datas[].name_food must exist in food_conditions[].
        Applied to CHOOSE sub-foods too.
      * Drop foods whose options reference orphan names; record the orphan
        in errors so the UI can surface it.
      * raw_menu is deep-copied before autofill mutation so the caller's copy
        (e.g. the raw download in the UI) stays untouched.
    """
    if not PYDANTIC_OK or not isinstance(raw_menu, dict):
        return raw_menu or {"food_conditions": [], "groups": []}, []

    # Deep-copy so autofill side-effects don't leak into the saved history.
    work = copy.deepcopy(raw_menu)

    # Safety net: derive missing food_conditions from options[].food_datas[]
    # BEFORE orphan check — otherwise every food with options would be dropped.
    auto_added = _autofill_food_conditions(work)

    errors: list = []
    if auto_added:
        errors.append(_err(
            "food_conditions", "<auto>",
            f"auto-derived {auto_added} entries from options (LLM forgot to populate)",
        ))

    # ---------- food_conditions ----------
    raw_fcs = work.get("food_conditions") or []
    if not isinstance(raw_fcs, list):
        return ({"food_conditions": [], "groups": []},
                [_err("food_conditions", "-", "food_conditions must be a list")])

    validated_fcs: list = []
    fc_name_lookup: dict = {}   # lower(name) -> validated dict
    for i, raw in enumerate(raw_fcs):
        if not isinstance(raw, dict):
            errors.append(_err(f"food_conditions[{i}]", raw, "not an object"))
            continue
        try:
            fc = FoodCondition(**raw).model_dump()
        except ValidationError as e:
            errors.append(_err(f"food_conditions[{i}]",
                               raw.get("name", "?"),
                               "; ".join(err["msg"] for err in e.errors())))
            continue
        key = fc["name"].lower()
        if key in fc_name_lookup:
            errors.append(_err(f"food_conditions[{i}]", fc["name"],
                               f"duplicate name (already at index {fc_name_lookup[key]['_idx']})"))
            continue
        fc_name_lookup[key] = {**fc, "_idx": i}
        validated_fcs.append(fc)

    def _orphans_in(food_dict: dict) -> list:
        """Return list of 'group.name' references not present in food_conditions."""
        out = []
        for og in food_dict.get("options") or []:
            for fd in og.get("food_datas") or []:
                nm = (fd.get("name_food") or "").strip()
                if nm.lower() not in fc_name_lookup:
                    out.append(f"{og.get('name')!r}.{nm!r}")
        return out

    # ---------- groups → foods ----------
    raw_groups = work.get("groups") or []
    if not isinstance(raw_groups, list):
        return ({"food_conditions": validated_fcs, "groups": []},
                errors + [_err("groups", "-", "groups must be a list")])

    validated_groups: list = []
    for gi, raw_g in enumerate(raw_groups):
        if not isinstance(raw_g, dict):
            errors.append(_err(f"groups[{gi}]", raw_g, "group is not an object"))
            continue

        raw_foods = raw_g.get("foods") or []
        kept_foods: list = []
        for fi, raw_f in enumerate(raw_foods):
            if not isinstance(raw_f, dict):
                errors.append(_err(f"groups[{gi}].foods[{fi}]", raw_f, "food is not an object"))
                continue
            try:
                food = Food(**raw_f).model_dump()
            except ValidationError as e:
                errors.append(_err(f"groups[{gi}].foods[{fi}]",
                                   raw_f.get("name", "?"),
                                   "; ".join(err["msg"] for err in e.errors())))
                continue

            # Cross-check: every food_data name_food must exist in food_conditions.
            # Applies to parent food AND every CHOOSE sub-food.
            orphans = _orphans_in(food)
            for ci, child in enumerate(food.get("foods") or []):
                for o in _orphans_in(child):
                    orphans.append(f"foods[{ci}].{o}")
            if orphans:
                errors.append(_err(
                    f"groups[{gi}].foods[{fi}]",
                    food.get("name", "?"),
                    f"options reference names not in food_conditions: {', '.join(orphans)}",
                ))
                continue
            kept_foods.append(food)

        if not kept_foods:
            errors.append(_err(f"groups[{gi}]", raw_g.get("name", "?"),
                               "group has no valid foods after validation"))
            continue
        try:
            group = Group(name=raw_g.get("name", ""),
                          description=raw_g.get("description"),
                          foods=kept_foods).model_dump()
        except ValidationError as e:
            errors.append(_err(f"groups[{gi}]", raw_g.get("name", "?"),
                               "; ".join(err["msg"] for err in e.errors())))
            continue
        validated_groups.append(group)

    return {"food_conditions": validated_fcs, "groups": validated_groups}, errors


def flatten_foods(menu: dict) -> List[dict]:
    """Flatten groups → foods (and CHOOSE sub-foods) into a flat list for scoring.

    A CHOOSE parent contributes one entry per sub-food as "<parent> - <sub-food>".
    OptionGroups (beilages) are NOT flattened (they are modifiers, not menu rows for matching).
    """
    out = []
    for g in (menu or {}).get("groups", []):
        gname = g.get("name", "")
        for f in g.get("foods", []):
            if f.get("kind") == KIND_CHOOSE and f.get("foods"):
                for c in f["foods"]:
                    out.append({
                        "group": gname,
                        "name":  f"{f.get('name','')} - {c.get('name','')}",
                        "price_in":  c.get("price_in"),
                        "price_out": c.get("price_out"),
                        "type": c.get("type", f.get("type")),
                        "kind": KIND_COMMON,
                    })
            else:
                out.append({
                    "group": gname,
                    "name":  f.get("name", ""),
                    "price_in":  f.get("price_in"),
                    "price_out": f.get("price_out"),
                    "type": f.get("type"),
                    "kind": f.get("kind"),
                })
    return out


def count_menu(menu: dict) -> dict:
    """Return display metrics: groups, foods, choose_kinds, sub_foods,
    option_groups, option_links, food_conditions, total_priced."""
    if not menu:
        menu = {}
    n_groups = n_foods = n_choose = 0
    n_sub_foods = 0
    n_option_groups = 0
    n_option_links = 0

    def _count_options(f: dict) -> None:
        nonlocal n_option_groups, n_option_links
        for og in f.get("options") or []:
            n_option_groups += 1
            n_option_links += len(og.get("food_datas") or [])

    for g in menu.get("groups", []):
        n_groups += 1
        for f in g.get("foods", []):
            n_foods += 1
            _count_options(f)
            if f.get("kind") == KIND_CHOOSE:
                n_choose += 1
                children = f.get("foods") or []
                n_sub_foods += len(children)
                for child in children:
                    _count_options(child)
    n_fc = len(menu.get("food_conditions") or [])
    return {
        "groups":          n_groups,
        "foods":           n_foods,
        "choose_kinds":    n_choose,
        "sub_foods":       n_sub_foods,
        "option_groups":   n_option_groups,
        "option_links":    n_option_links,
        "food_conditions": n_fc,
        # POS rows = COMMON foods + CHOOSE sub-foods (CHOOSE parent itself has no price)
        "total_priced":    n_foods - n_choose + n_sub_foods,
    }
