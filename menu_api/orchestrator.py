"""End-to-end import: validated menu → POST in 3 phases.

  Phase 1 — food_conditions (one POST per unique modifier)
            Build {name_lower: id} map for the next phases.

  Phase 2 — groups (one POST per section)
            Capture each group's id for the next phase.

  Phase 3 — foods (one POST per food, optionally followed by combo sub-foods).
            food.beilages[].food_datas are resolved via the FC id map from
            phase 1. A `kind=5` food spawns one extra POST per combo_item
            with `parentId` set to the parent's id.

Failure policy = SKIP + REPORT. A single item failure never aborts the run;
every failure is recorded in the report so the caller can retry / clean up.
"""
from typing import Optional

from menu_api.cms_client import CMSClient, error_message, extract_id, is_success
from menu_api.mapper import (
    map_combo_item, map_food, map_food_condition, map_group_food,
)
from menu_api.settings import settings


def _new_section():
    return {"created": [], "failed": []}


def _new_report():
    return {
        "food_conditions": _new_section(),
        "groups":          _new_section(),
        "foods":           _new_section(),
        "combo_items":     _new_section(),
    }


def _summarize(report: dict) -> dict:
    return {
        section: {
            "ok":   len(report[section]["created"]),
            "fail": len(report[section]["failed"]),
        }
        for section in ("food_conditions", "groups", "foods", "combo_items")
    }


async def _create_food_conditions(cms: CMSClient, fcs: list, restaurant_id: int):
    """Phase 1. Returns (fc_id_map, section_report)."""
    section = _new_section()
    fc_id_map: dict = {}
    for fc in fcs or []:
        try:
            payload = map_food_condition(fc, restaurant_id)
            resp = await cms.create_food_condition(payload)
        except Exception as e:
            section["failed"].append({"name": fc.get("name"), "error": str(e)[:300]})
            continue
        new_id = extract_id(resp)
        if is_success(resp) and new_id:
            fc_id_map[(fc.get("name") or "").lower()] = new_id
            section["created"].append({"name": fc.get("name"), "id": new_id})
        else:
            section["failed"].append({"name": fc.get("name"), "error": error_message(resp)})
    return fc_id_map, section


async def _create_combo_items(cms: CMSClient, food: dict, group_id, parent_id,
                              restaurant_id: int, section: dict):
    """Phase 3b. Mutates `section` in-place."""
    for ci in food.get("combo_items") or []:
        try:
            payload = map_combo_item(ci, {
                "groupFoodId":  group_id,
                "restaurantId": restaurant_id,
                "parentId":     parent_id,
                "parentType":   food.get("type", 2),
            })
            resp = await cms.create_food(payload)
        except Exception as e:
            section["failed"].append({
                "name": ci.get("name"), "parent": food.get("name"),
                "error": str(e)[:300],
            })
            continue
        new_id = extract_id(resp)
        if is_success(resp) and new_id:
            section["created"].append({
                "name": ci.get("name"), "parent": food.get("name"), "id": new_id,
            })
        else:
            section["failed"].append({
                "name": ci.get("name"), "parent": food.get("name"),
                "error": error_message(resp),
            })


async def _create_group_and_foods(cms: CMSClient, group: dict, restaurant_id: int,
                                  fc_id_map: dict, report: dict):
    """Phase 2 + 3 for one group. Mutates `report`."""
    try:
        gresp = await cms.create_group_food(map_group_food(group))
    except Exception as e:
        report["groups"]["failed"].append({"name": group.get("name"), "error": str(e)[:300]})
        return

    group_id = extract_id(gresp)
    if not (is_success(gresp) and group_id):
        report["groups"]["failed"].append({
            "name": group.get("name"), "error": error_message(gresp),
        })
        return
    report["groups"]["created"].append({"name": group.get("name"), "id": group_id})

    for food in group.get("foods") or []:
        try:
            payload = map_food(food, {
                "groupFoodId":  group_id,
                "restaurantId": restaurant_id,
            }, fc_id_map)
            fresp = await cms.create_food(payload)
        except Exception as e:
            report["foods"]["failed"].append({
                "name": food.get("name"), "group": group.get("name"),
                "error": str(e)[:300],
            })
            continue

        food_id = extract_id(fresp)
        if not (is_success(fresp) and food_id):
            report["foods"]["failed"].append({
                "name": food.get("name"), "group": group.get("name"),
                "error": error_message(fresp),
            })
            continue
        report["foods"]["created"].append({
            "name": food.get("name"), "group": group.get("name"), "id": food_id,
        })

        # Combo items only for kind=5
        if food.get("kind") == 5 and food.get("combo_items"):
            await _create_combo_items(
                cms, food, group_id, food_id, restaurant_id, report["combo_items"],
            )


async def import_menu(menu: dict, restaurant_id: Optional[int] = None) -> dict:
    """Run the full create pipeline. Returns the report (no exceptions on item failure)."""
    rid = restaurant_id or int(settings.CMS_RESTAURANT_ID or 0)
    if not rid:
        raise RuntimeError("CMS_RESTAURANT_ID not configured")

    report = _new_report()

    async with CMSClient() as cms:
        # Phase 1
        fc_id_map, report["food_conditions"] = await _create_food_conditions(
            cms, menu.get("food_conditions") or [], rid,
        )
        # Phase 2 + 3
        for group in menu.get("groups") or []:
            await _create_group_and_foods(cms, group, rid, fc_id_map, report)

    report["summary"] = _summarize(report)
    return report
