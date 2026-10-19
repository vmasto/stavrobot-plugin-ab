#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_client as ab  # noqa: E402


def main(params: dict) -> dict:
    days = ab.int_param(params, "days", 4, 1, 14)
    result = ab.gql("GetTimeSlotsV2", ab.SLOTS, {"lang": ab.LANG}).get("getTimeSlots") or {}
    out = []
    for day in (result.get("slots") or [])[:days]:
        free = []
        for s in day.get("dayTimeSlots") or []:
            if not s.get("available") or s.get("disabled") or not s.get("slotCode"):
                continue
            row = {
                "slot_code": s.get("slotCode"),
                "time": s.get("slotTimeRangeFmt"),
                "order_by": s.get("cutOffTime"),
                "cost": ((s.get("preparationCost") or {}).get("deliveryCost") or {}).get("formattedValue"),
                "note": s.get("localizedMessage") or None,
            }
            free.append({key: value for key, value in row.items() if value is not None})
        out.append({"date": day.get("formattedDate") or day.get("date"), "slots": free})
    return {"days": out}


if __name__ == "__main__":
    ab.run(main)
