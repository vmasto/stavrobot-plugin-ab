#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_client as ab  # noqa: E402


def main(params: dict) -> dict:
    variables = {"timeslotId": ab.text_param(params, "slot_code"), "lang": ab.LANG}
    cart = ab.gql("SelectTimeSlotV2", ab.SELECT_SLOT, variables).get("cart") or {}
    chosen = ab.slot(cart.get("timeslot"))
    if not chosen:
        raise ab.ToolError("ab.gr didn't confirm the slot. Check delivery_slots again.")
    return {"delivery_slot": chosen}


if __name__ == "__main__":
    ab.run(main)
