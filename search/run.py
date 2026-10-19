#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_client as ab  # noqa: E402


def main(params: dict) -> dict:
    variables = {
        "lang": ab.LANG,
        "searchQuery": ab.text_param(params, "query"),
        "pageSize": ab.int_param(params, "max_results", 10, 1, 50),
        "pageNumber": ab.int_param(params, "page", 0, 0, 100),
    }
    result = ab.gql("GetProductSearch", ab.SEARCH, variables).get("productSearch") or {}
    pagination = result.get("pagination") or {}
    out = {"products": [ab.product(p) for p in result.get("products") or []], "total": pagination.get("totalResults")}
    page = pagination.get("currentPage") or 0
    if page + 1 < (pagination.get("totalPages") or 0):
        out["next_page"] = page + 1
    return out


if __name__ == "__main__":
    ab.run(main)
