#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_client as ab  # noqa: E402


def main(params: dict) -> dict:
    query = ab.text_param(params, "query", required=False)
    limit = ab.int_param(params, "max_results", 50, 1, 200)
    variables = {
        "orderCode": ab.LAST_ORDERS_PRODUCTS,
        "searchQuery": query or "",
        "pageSize": limit,
        "pageNumber": 0,
        "lang": ab.LANG,
    }
    result = ab.gql("groupedProductsForQuickShopOrders", ab.QUICK_SHOP, variables).get("groupedProductsForQuickShopOrders") or {}
    # Purchase counts come from cached past orders only, so this stays one API call.
    stats = ab.product_stats(list(ab.load_cache().values()))
    products = []
    for group in result.get("groupedProducts") or []:
        for item in group.get("products") or []:
            row = ab.product(item)
            row["category"] = group.get("name")
            for key in ("times_bought", "last_bought", "every_days", "usual_quantity"):
                if key in stats.get(row.get("code"), {}):
                    row[key] = stats[row["code"]][key]
            products.append(row)
    products.sort(key=lambda p: -p.get("times_bought", 0))
    out = {"products": products[:limit], "total": (result.get("pagination") or {}).get("totalResults")}
    if not stats:
        out["note"] = "No purchase counts yet. Run purchase_history once to load past orders."
    return out


if __name__ == "__main__":
    ab.run(main)
