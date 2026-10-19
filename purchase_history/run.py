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
    orders, incomplete = ab.history(ab.int_param(params, "months", 6, 1, 24))
    rows = list(ab.product_stats(orders).values())
    if query:
        rows = [r for r in rows if ab.matches(query, r.get("name"), r.get("brand"))]
    if params.get("due_only"):
        rows = [r for r in rows if r.get("due")]
        rows.sort(key=lambda r: -r["days_since_last"] / max(r.get("every_days") or 1, 1))
    else:
        rows.sort(key=lambda r: (-r["times_bought"], r["days_since_last"]))
    out = {"products": rows[: ab.int_param(params, "max_results", 100, 1, 300)], "orders_counted": len(orders)}
    if incomplete:
        out["note"] = "Some older orders aren't loaded yet. Call again to load more."
    return out


if __name__ == "__main__":
    ab.run(main)
