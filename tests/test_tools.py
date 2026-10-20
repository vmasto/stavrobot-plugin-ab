"""Offline tests against a fake ab.gr API.

Run with: uv run --with pytest --with requests pytest tests
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ab_client as ab  # noqa: E402

COOKIE = "Cookie: _abck=x; grocery-roatc=access; grocery-rortc=refresh"


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.ok = status < 400
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def cart_entry(code, quantity, name="Προϊόν"):
    return {"entryNumber": 0, "quantity": quantity, "product": {"code": code, "name": name}, "totalPrice": {}}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"cookie": COOKIE, "permissions": ["*"]}))
    monkeypatch.setattr(ab, "CONFIG_PATH", config)
    monkeypatch.setattr(ab, "CACHE_PATH", tmp_path / "order_cache.json")
    monkeypatch.setattr(ab, "_refreshed", False)
    monkeypatch.setattr(ab, "session", ab.requests.Session())

    calls, routes = [], {}

    def request(method, url, **kwargs):
        assert method == "POST" and url == ab.API_URL
        assert 0 < kwargs["timeout"] <= ab.TIMEOUT
        body = kwargs["json"]
        calls.append({**body, "headers": kwargs["headers"]})
        handler = routes[body["operationName"]]
        result = handler(body["variables"]) if callable(handler) else handler
        return result if isinstance(result, Response) else Response(200, {"data": result})

    monkeypatch.setattr(ab.session, "request", request)
    ab.load_cookies()
    return type("Fake", (), {"routes": routes, "calls": calls, "config": config})


def tool(name):
    spec = importlib.util.spec_from_file_location(f"tool_{name}", ROOT / name / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def ops(fake):
    return [call["operationName"] for call in fake.calls]


def test_cookie_header_is_parsed_and_renewed_cookies_are_saved(fake):
    assert ab.session.cookies.get("grocery-rortc", domain=".ab.gr") == "refresh"
    ab.session.cookies.set("grocery-roatc", "renewed", domain=".ab.gr", path="/")
    ab.save_cookies()
    saved = json.loads(fake.config.read_text())
    assert saved["session_cookies"]["grocery-roatc"] == "renewed"
    assert saved["permissions"] == ["*"] and saved["cookie"] == COOKIE

    # The next run starts from the renewed cookies, not the pasted header.
    ab.session = ab.requests.Session()
    ab.load_cookies()
    assert ab.session.cookies.get("grocery-roatc", domain=".ab.gr") == "renewed"


def test_a_newly_pasted_cookie_replaces_the_saved_session(fake):
    ab.save_cookies()
    config = json.loads(fake.config.read_text())
    config["cookie"] = "grocery-rortc=pasted-again"
    fake.config.write_text(json.dumps(config))
    ab.session = ab.requests.Session()
    ab.load_cookies()
    assert ab.session.cookies.get("grocery-rortc", domain=".ab.gr") == "pasted-again"
    assert ab.session.cookies.get("_abck", domain=".ab.gr") is None


def test_missing_login_cookies_explain_the_setup(fake):
    fake.config.write_text(json.dumps({"cookie": "_abck=x"}))
    with pytest.raises(ab.ToolError, match="Cookie request header"):
        ab.load_cookies()


def test_an_expired_login_is_refreshed_once_and_retried(fake):
    answers = iter(
        [
            Response(200, {"errors": [{"message": "Unauthorized", "extensions": {"response": {"status": 401}}}]}),
            {"cart": {"entries": [], "totalPrice": {"formattedValue": "€0,00"}}},
        ]
    )
    fake.routes["getCartForCheckout"] = lambda variables: next(answers)
    fake.routes["RefreshCustomerToken"] = {"refreshCustomerAuthCookies": True}
    out = tool("cart")({})
    assert out["total"] == "€0,00"
    assert ops(fake) == ["getCartForCheckout", "RefreshCustomerToken", "getCartForCheckout"]
    assert fake.calls[1]["headers"]["x-do-refresh-token"] == "true"


def test_a_failed_refresh_asks_for_a_new_cookie(fake):
    fake.routes["getCartForCheckout"] = Response(401, {"errors": [{"message": "Unauthorized"}]})
    fake.routes["RefreshCustomerToken"] = Response(200, {"errors": [{"message": "TOKEN_REVOKED"}]})
    with pytest.raises(ab.ToolError, match="logged the plugin out"):
        tool("cart")({})


def test_update_cart_sends_differences_and_removes_zeroes(fake):
    carts = iter(
        [
            {"cart": {"entries": [cart_entry("A", 2), cart_entry("C", 1)]}},
            {"cart": {"entries": [cart_entry("A", 5), cart_entry("B", 1)], "totalPrice": {"formattedValue": "€9,00"}}},
        ]
    )
    fake.routes["getCartForCheckout"] = lambda variables: next(carts)
    fake.routes["DeleteItem"] = {"cart": {"totalUnitCount": 2}}
    fake.routes["MiniBasketUpdateCartEntryQuantity"] = {
        "cart": {"cartModifications": [{"productCode": "B", "statusCode": "success"}]}
    }
    out = tool("update_cart")(
        {"items": [{"code": "A", "quantity": 5}, {"code": "B", "quantity": 1}, {"code": "C", "quantity": 0}, {"code": "D", "quantity": 0}]}
    )
    update = next(c for c in fake.calls if c["operationName"] == "MiniBasketUpdateCartEntryQuantity")
    assert update["variables"]["entries"] == [
        {"productCode": "A", "quantityDelta": "3"},
        {"productCode": "B", "quantityDelta": "1"},
    ]
    deletes = [c["variables"]["productCode"] for c in fake.calls if c["operationName"] == "DeleteItem"]
    assert deletes == ["C"]
    assert out["total"] == "€9,00" and "problems" not in out


def test_update_cart_reports_items_the_store_refused(fake):
    fake.routes["getCartForCheckout"] = {"cart": {"entries": []}}
    fake.routes["MiniBasketUpdateCartEntryQuantity"] = {
        "cart": {"cartModifications": [{"productCode": "A", "statusCode": "noStock", "quantityAdded": 0}]}
    }
    out = tool("update_cart")({"items": [{"code": "A", "quantity": 1}]})
    assert out["problems"] == [{"code": "A", "status": "noStock", "quantity_added": 0}]


def order(code, days, items, status="COMPLETED"):
    return {
        "code": code,
        "created": days_ago(days),
        "status": status,
        "entries": [cart_entry(c, q, name) for c, q, name in items],
    }


def history_routes(fake, orders):
    fake.routes["MyOrdersGetOrders"] = {
        "getOrders": {"orders": [{k: o[k] for k in ("code", "created", "status")} for o in orders]}
    }
    fake.routes["OrderDetails"] = lambda variables: {"order": next(o for o in orders if o["code"] == variables["code"])}


def test_purchase_history_counts_regulars_and_flags_due_ones(fake):
    bananas = ("B1", 6, "Μπανάνες Chiquita")
    history_routes(
        fake,
        [
            order("o4", 2, [("M1", 2, "Γάλα")], status="CANCELLED"),
            order("o3", 15, [bananas, ("M1", 1, "Γάλα")]),
            order("o2", 22, [bananas]),
            order("o1", 29, [bananas, ("M1", 1, "Γάλα")]),
        ],
    )
    out = tool("purchase_history")({})
    rows = {r["code"]: r for r in out["products"]}
    assert rows["B1"]["times_bought"] == 3 and rows["B1"]["every_days"] == 7 and rows["B1"]["due"] is True
    assert rows["B1"]["usual_quantity"] == 6
    # The cancelled order doesn't count.
    assert rows["M1"]["times_bought"] == 2 and rows["M1"]["due"] is False
    assert [r["code"] for r in tool("purchase_history")({"due_only": True})["products"]] == ["B1"]
    assert [r["code"] for r in tool("purchase_history")({"query": "μπανανα"})["products"]] == ["B1"]


def test_items_not_bought_for_three_usual_gaps_are_stopped_not_due(fake):
    history_routes(fake, [order(f"o{n}", days, [("T1", 1, "Ice tea")]) for n, days in enumerate((80, 87, 94))])
    row = tool("purchase_history")({})["products"][0]
    assert row["every_days"] == 7 and row["due"] is False and row["stopped"] is True


def test_order_dates_in_the_sites_local_format_are_read():
    parsed = ab.parse_date("19/10/2026 20:38:16")
    assert parsed.isoformat() == "2026-10-19T20:38:16+03:00"


def test_slot_cutoffs_are_converted_from_utc_to_athens_time():
    assert ab.cutoff("20/10/2026 09:00:00") == "20/10/2026 12:00"
    assert ab.cutoff("01/12/2026 09:00:00") == "01/12/2026 11:00"


def test_finished_orders_are_cached_and_open_ones_fetched_again(fake):
    history_routes(fake, [order("open", 1, [("A", 1, "x")], status="CREATED"), order("done", 9, [("A", 1, "x")])])
    tool("purchase_history")({})
    tool("purchase_history")({})
    fetched = [c["variables"]["code"] for c in fake.calls if c["operationName"] == "OrderDetails"]
    assert sorted(fetched) == ["done", "open", "open"]


def test_my_products_adds_purchase_counts_from_the_cache(fake):
    cache = {code: order(code, days, [("B2", 1, "b")]) for code, days in (("o1", 20), ("o2", 10))}
    for cached in cache.values():
        cached["entries"] = [ab.entry(e) for e in cached["entries"]]
    ab.CACHE_PATH.write_text(json.dumps(cache))
    fake.routes["groupedProductsForQuickShopOrders"] = {
        "groupedProductsForQuickShopOrders": {
            "groupedProducts": [
                {"name": "Φρούτα", "products": [{"code": "B1", "name": "Μπανάνες Bio"}, {"code": "B2", "name": "Μπανάνες Chiquita"}]}
            ],
            "pagination": {"totalResults": 2},
        }
    }
    out = tool("my_products")({"query": "μπανάνες"})
    assert [p["code"] for p in out["products"]] == ["B2", "B1"]
    assert out["products"][0]["times_bought"] == 2 and out["products"][0]["category"] == "Φρούτα"
    call = fake.calls[-1]
    assert call["variables"]["orderCode"] == "LAST_ORDERS_PRODUCTS" and call["variables"]["searchQuery"] == "μπανάνες"


def checkout_routes(fake, slot=True, valid=True):
    timeslot = {"slotCode": "S1", "dateFmt": "Τετ 21/10", "slotTimeRangeFmt": "10:00 - 12:00"} if slot else None
    fake.routes["validateCartBeforeCheckout"] = {"result": {"isValid": valid, "reason": None if valid else "x"}}
    fake.routes["BasketMinimumAmount"] = {"cart": {"minimumSpendReached": True}}
    fake.routes["getCartForCheckout"] = {"cart": {"guid": "g1", "entries": [cart_entry("A", 2)], "timeslot": timeslot}}
    fake.routes["getCheckoutInfo"] = {
        "checkout": {
            "paymentMethods": {
                "paymentMethods": [
                    {"delhaizePaymentTypeCode": "CARD", "delhaizePaymentTypeName": "Κάρτα", "onlinePayment": True},
                    {"delhaizePaymentTypeCode": "POS", "delhaizePaymentTypeName": "Κάρτα στην παράδοση", "onlinePayment": False},
                ]
            },
            "cart": {"guid": "g1", "totalPriceFinal": {"formattedValue": "€42,00"}, "timeslot": timeslot},
        }
    }


def test_preview_lists_pay_on_delivery_methods_and_a_confirmation(fake):
    checkout_routes(fake)
    out = tool("checkout_preview")({})
    assert out["pay_on_delivery_methods"] == [{"code": "POS", "name": "Κάρτα στην παράδοση"}]
    assert out["total"] == "€42,00" and len(out["confirmation"]) == 8 and "problems" not in out


def test_preview_without_a_slot_gives_no_confirmation(fake):
    checkout_routes(fake, slot=False)
    out = tool("checkout_preview")({})
    assert "confirmation" not in out and any("delivery slot" in p for p in out["problems"])


def test_place_order_refuses_a_stale_confirmation(fake):
    checkout_routes(fake)
    with pytest.raises(ab.ToolError, match="changed since that preview"):
        tool("place_order")({"confirmation": "deadbeef", "payment_method": "POS"})
    assert "placeOrder" not in ops(fake)


def test_place_order_refuses_online_payment(fake):
    checkout_routes(fake)
    code = tool("checkout_preview")({})["confirmation"]
    with pytest.raises(ab.ToolError, match="must be one of POS"):
        tool("place_order")({"confirmation": code, "payment_method": "CARD"})
    assert "placeOrder" not in ops(fake)


def test_place_order_pays_on_delivery(fake):
    checkout_routes(fake)
    code = tool("checkout_preview")({})["confirmation"]
    fake.routes["placeOrder"] = {"placeOrderV2": {"order": "000123", "errors": []}}
    out = tool("place_order")({"confirmation": code, "payment_method": "POS"})
    assert out["order_code"] == "000123" and out["total"] == "€42,00"
    sent = next(c for c in fake.calls if c["operationName"] == "placeOrder")["variables"]
    assert sent["input"]["delhaizePaymentType"] == "POS" and sent["input"]["substitutionAllowed"] is False
    assert sent["channel"] == "web" and sent["propositionType"] == "ECOM"


def test_api_errors_show_the_inner_message(fake):
    fake.routes["GetProductSearch"] = Response(
        200,
        {
            "errors": [
                {
                    "message": "outer",
                    "reasonCode": "genericError",
                    "extensions": {"response": {"status": 400, "body": {"errors": [{"message": "Language  el is not supported"}]}}},
                }
            ]
        },
    )
    with pytest.raises(ab.ToolError, match="Language  el is not supported"):
        tool("search")({"query": "γάλα"})


def test_manifests_list_every_tool_and_scripts_are_executable():
    tools = sorted(p.parent.name for p in ROOT.glob("*/run.py"))
    assert len(tools) == 12
    for name in tools:
        manifest = json.loads((ROOT / name / "manifest.json").read_text())
        assert manifest["name"] == name and manifest["entrypoint"] == "run.py"
        assert (ROOT / name / "run.py").stat().st_mode & 0o111
