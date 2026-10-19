#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_client as ab  # noqa: E402


def main(params: dict) -> dict:
    confirmation = ab.text_param(params, "confirmation")
    method = ab.text_param(params, "payment_method")
    preview = ab.checkout_state()
    if preview.get("problems"):
        raise ab.ToolError("Can't order yet: " + " ".join(preview["problems"]))
    if preview.get("confirmation") != confirmation:
        raise ab.ToolError(
            "The cart, total or slot changed since that preview. Run checkout_preview again and show it to the user."
        )
    codes = [m["code"] for m in preview["pay_on_delivery_methods"]]
    if method not in codes:
        raise ab.ToolError(f"payment_method must be one of {', '.join(codes)}. Online card payment isn't supported.")
    variables = {
        "input": {
            "delhaizePaymentType": method,
            "substitutionAllowed": bool(params.get("allow_substitutes")),
            "paperInvoice": False,
            "originalServerName": "www.ab.gr",
        },
        "channel": ab.CHANNEL,
        "lang": ab.LANG,
        "propositionType": ab.PROPOSITION_TYPE,
    }
    result = ab.gql("placeOrder", ab.PLACE_ORDER, variables).get("placeOrderV2") or {}
    errors = [e.get("message") or e.get("reason") for e in result.get("errors") or []]
    if errors or not result.get("order"):
        reason = "; ".join(filter(None, errors)) or result.get("message") or result.get("reasonCode") or "no reason given"
        raise ab.ToolError(f"ab.gr didn't place the order: {reason}")
    return {
        "order_code": result["order"],
        "total": preview.get("total"),
        "delivery_slot": preview.get("delivery_slot"),
        "payment_method": method,
    }


if __name__ == "__main__":
    ab.run(main)
