#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_client as ab  # noqa: E402


def main(params: dict) -> dict:
    items = params.get("items")
    if not isinstance(items, list) or not items:
        raise ab.ToolError("items must be a list of {code, quantity}.")
    wanted = {}
    for item in items:
        if not isinstance(item, dict) or not str(item.get("code") or "").strip():
            raise ab.ToolError("Each item needs a code.")
        quantity = item.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or quantity < 0:
            raise ab.ToolError(f"quantity for {item.get('code')} must be 0 or more.")
        wanted[str(item["code"]).strip()] = quantity
    current = {}
    for e in ab.get_cart().get("entries") or []:
        code = (e.get("product") or {}).get("code")
        if code:
            current[code] = e.get("quantity") or 0
    changes, problems = [], []
    for code, quantity in wanted.items():
        if quantity == 0:
            if code in current:
                ab.gql("DeleteItem", ab.DELETE_ITEM, {"lang": ab.LANG, "productCode": code})
            continue
        delta = quantity - current.get(code, 0)
        if delta:
            changes.append({"productCode": code, "quantityDelta": ab.number_text(delta)})
    if changes:
        variables = {"lang": ab.LANG, "entries": changes}
        result = ab.gql("MiniBasketUpdateCartEntryQuantity", ab.UPDATE_CART, variables).get("cart") or {}
        for change in result.get("cartModifications") or []:
            status = str(change.get("statusCode") or "")
            if status and status.lower() != "success":
                problems.append(
                    {"code": change.get("productCode"), "status": status, "quantity_added": change.get("quantityAdded")}
                )
    out = ab.cart_summary(ab.get_cart())
    if problems:
        out["problems"] = problems
    return out


if __name__ == "__main__":
    ab.run(main)
