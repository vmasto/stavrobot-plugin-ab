#!/usr/bin/env -S uv run
# /// script
# dependencies = ["requests"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_client as ab  # noqa: E402


def main(params: dict) -> dict:
    user = ab.gql("CiamUser", ab.CUSTOMER).get("CiamUser") or {}
    if not user:
        raise ab.ToolError(ab.SETUP_HELP)
    name = user.get("name") or {}
    return {
        "logged_in": True,
        "name": " ".join(filter(None, [name.get("first_name"), name.get("last_name")])) or None,
        "email": (user.get("email") or {}).get("value"),
    }


if __name__ == "__main__":
    ab.run(main)
