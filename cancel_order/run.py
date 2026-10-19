#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_client as ab  # noqa: E402


def main(params: dict) -> dict:
    code = ab.text_param(params, "order_code")
    result = ab.gql("cancelOrder", ab.CANCEL_ORDER, {"code": code}).get("cancelOrder") or {}
    errors = [e.get("message") or e.get("reason") for e in result.get("errors") or []]
    if errors:
        raise ab.ToolError("ab.gr didn't cancel the order: " + "; ".join(filter(None, errors)))
    return {"order_code": code, "cancelled": True}


if __name__ == "__main__":
    ab.run(main)
