#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_client as ab  # noqa: E402


def main(params: dict) -> dict:
    code = ab.text_param(params, "order_code", required=False)
    if code:
        order = ab.order_details(code)
        if not order:
            raise ab.ToolError(f"No order {code}.")
        return {
            "order_code": order.get("code"),
            "created": order.get("created"),
            "status": order.get("statusDisplay") or order.get("status"),
            "delivery": order.get("slotData"),
            "payment": order.get("paymentType"),
            "total": ab.money(order.get("totalPriceFinal")) or ab.money(order.get("totalPrice")),
            "cancellable": order.get("cancellable"),
            "items": [ab.entry(e) for e in order.get("entries") or []],
        }
    return {
        "orders": [
            {
                "order_code": o.get("code"),
                "created": o.get("created"),
                "status": o.get("status"),
                "delivery_status": o.get("deliveryStatus"),
                "delivery": o.get("slotData"),
                "items": o.get("totalItems"),
                "total": ab.money(o.get("totalPrice")),
            }
            for o in ab.list_orders(ab.int_param(params, "max_results", 10, 1, 50))
        ]
    }


if __name__ == "__main__":
    ab.run(main)
