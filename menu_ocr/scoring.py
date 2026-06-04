"""Score a predicted menu against a ground-truth file."""
from menu_ocr.validation import flatten_foods


def score_against_truth(pred_menu, truth_data):
    """Score prediction against ground truth.

    `pred_menu` is a dict {"groups": [...]} from validate_menu.
    `truth_data` may be either:
      - a flat list [{name, price_in OR price, ...}] (legacy format), or
      - a dict {"groups": [...]} in the same nested format.
    Matching is done on flattened (parent + variant) names.
    """
    if not pred_menu or not pred_menu.get("groups"):
        return None

    pred_flat = flatten_foods(pred_menu)

    if isinstance(truth_data, dict) and "groups" in truth_data:
        truth_flat = flatten_foods(truth_data)
    elif isinstance(truth_data, list):
        # Legacy: each entry has name + price. Treat price as price_in.
        truth_flat = []
        for it in truth_data:
            if not isinstance(it, dict):
                continue
            price = it.get("price_in") if "price_in" in it else it.get("price")
            truth_flat.append({"name": it.get("name", ""), "price_in": price})
    else:
        return None

    def norm(x):
        return str(x.get("name", "")).strip().lower()

    pred_map  = {norm(it): it for it in pred_flat  if it.get("name")}
    truth_map = {norm(it): it for it in truth_flat if it.get("name")}
    matched   = set(pred_map) & set(truth_map)

    price_ok = 0
    for nm in matched:
        try:
            p_pred  = float(pred_map[nm].get("price_in")  or -1)
            p_truth = float(truth_map[nm].get("price_in") or -2)
            if abs(p_pred - p_truth) < 0.01:
                price_ok += 1
        except Exception:
            pass

    total = len(truth_map) or 1
    return {
        "n_pred": len(pred_flat), "n_truth": len(truth_flat),
        "name_match": len(matched),
        "name_recall": round(len(matched) / total * 100, 1),
        "price_match": price_ok,
        "price_acc": round(price_ok / total * 100, 1),
        "missing": sorted(set(truth_map) - set(pred_map)),
        "extra":   sorted(set(pred_map) - set(truth_map)),
    }
