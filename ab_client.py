"""Shared ab.gr client for the plugin's tools.

ab.gr has no public API. The site talks to a GraphQL endpoint at /api/v1/, and the
queries below are trimmed copies of the ones in the site's own JavaScript.
"""

import hashlib
import json
import statistics
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

PLUGIN_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PLUGIN_ROOT / "config.json"
# Delivered orders don't change, so their contents are kept here instead of being
# fetched again on every purchase_history call.
CACHE_PATH = PLUGIN_ROOT / "order_cache.json"

SITE = "https://www.ab.gr"
API_URL = SITE + "/api/v1/"
LANG = "gr"
CHANNEL = "web"
PROPOSITION_TYPE = "ECOM"
# The quick shop page lists everything bought in recent orders under this code.
LAST_ORDERS_PRODUCTS = "LAST_ORDERS_PRODUCTS"
# The cookies that carry the login. The access cookie is short-lived, the refresh
# cookie renews it.
AUTH_COOKIES = ("grocery-roatc", "grocery-rortc")
FINISHED_STATUSES = {"COMPLETED", "DELIVERED", "CANCELLED", "AUTO_CLOSED", "CLOSED"}
CANCELLED_STATUSES = {"CANCELLED", "AUTO_CLOSED"}
# Order dates come as local time without a zone, e.g. 19/10/2026 20:38:16.
STORE_TIMEZONE = ZoneInfo("Europe/Athens")

# The plugin-runner kills a tool after 30 seconds, so give up a bit earlier
# with a readable error instead.
DEADLINE_SECONDS = 25
TIMEOUT = 15

SETUP_HELP = (
    "Not logged in to ab.gr. Log in at https://www.ab.gr in a desktop browser, open the developer "
    "tools on the Network tab, click any request to www.ab.gr/api/v1/ and copy the value of its "
    "Cookie request header. Paste it into the ab plugin's cookie setting in /settings/plugins, "
    "not into the chat."
)

_started = time.monotonic()
session = requests.Session()
_refreshed = False


class ToolError(Exception):
    pass


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        return {}
    except ValueError:
        raise ToolError("config.json is not valid JSON.")


def save_config(updates: dict) -> None:
    # Read again right before writing so keys set elsewhere, like permissions, survive.
    config = load_config()
    config.update(updates)
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def parse_cookie_header(text: str) -> dict:
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):]
    cookies = {}
    for part in text.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies[name.strip()] = value.strip()
    return cookies


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_cookies() -> None:
    """Put the saved login cookies in the session.

    The user pastes a Cookie header into `cookie`. ab.gr renews the cookies on later
    calls, so the plugin keeps the current set in `session_cookies` and only goes back
    to `cookie` when the user pastes a new one.
    """
    config = load_config()
    pasted = str(config.get("cookie") or "").strip()
    if not pasted:
        raise ToolError(SETUP_HELP)
    saved = config.get("session_cookies")
    if config.get("cookie_digest") == digest(pasted) and isinstance(saved, dict) and saved:
        cookies = saved
    else:
        cookies = parse_cookie_header(pasted)
    if not any(name in cookies for name in AUTH_COOKIES):
        raise ToolError(SETUP_HELP)
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".ab.gr", path="/")


def save_cookies() -> None:
    cookies = {}
    # A cookie the site sets for www.ab.gr can sit next to the copy for .ab.gr. Keep
    # the one the site set most recently, which requests lists last.
    for cookie in session.cookies:
        cookies[cookie.name] = cookie.value
    config = load_config()
    pasted = str(config.get("cookie") or "").strip()
    if pasted and cookies and cookies != config.get("session_cookies"):
        save_config({"session_cookies": cookies, "cookie_digest": digest(pasted)})


def send(body: dict, extra_headers: dict | None = None) -> requests.Response:
    remaining = DEADLINE_SECONDS - (time.monotonic() - _started)
    if remaining < 1:
        raise ToolError("Ran out of time talking to ab.gr, try again.")
    headers = {
        "Content-Type": "application/json",
        "Accept-Language": "el",
        "Origin": SITE,
        "Referer": SITE + "/",
        "x-apollo-operation-name": body["operationName"],
        **(extra_headers or {}),
    }
    try:
        return session.request("POST", API_URL, json=body, headers=headers, timeout=min(TIMEOUT, remaining))
    except requests.Timeout:
        raise ToolError("ab.gr did not respond in time.")
    except requests.RequestException as error:
        raise ToolError(f"Network error talking to ab.gr ({type(error).__name__}).")


def error_text(errors: list) -> str:
    messages = []
    for error in errors:
        inner = (error.get("extensions") or {}).get("response", {}).get("body", {}).get("errors") or []
        text = "; ".join(e.get("message", "") for e in inner if e.get("message")) or error.get("message", "")
        reason = error.get("reasonCode")
        if reason and reason != "genericError":
            text = f"{text} ({reason})"
        messages.append(text.strip())
    return "; ".join(m for m in messages if m) or "unknown error"


def is_auth_error(response: requests.Response, errors: list) -> bool:
    if response.status_code in (401, 403):
        return True
    for error in errors:
        extensions = error.get("extensions") or {}
        status = (extensions.get("response") or {}).get("status")
        code = str(extensions.get("code") or "") + " " + str(error.get("reasonCode") or "")
        if status in (401, 403) or any(word in code.upper() for word in ("UNAUTH", "FORBIDDEN", "TOKEN")):
            return True
    return False


def refresh() -> None:
    global _refreshed
    _refreshed = True
    # The site sends this header when it renews the login cookies.
    body = {"operationName": "RefreshCustomerToken", "query": REFRESH, "variables": {}}
    response = send(body, {"x-do-refresh-token": "true"})
    body = json_body(response)
    if body.get("errors") or not response.ok:
        raise ToolError("ab.gr logged the plugin out. " + SETUP_HELP)


def json_body(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        if response.status_code == 403:
            raise ToolError("ab.gr's bot protection blocked the request (HTTP 403). Try again later.")
        raise ToolError(f"ab.gr returned HTTP {response.status_code} without JSON.")
    return body if isinstance(body, dict) else {}


def gql(name: str, query: str, variables: dict | None = None) -> dict:
    body = {"operationName": name, "query": query, "variables": variables or {}}
    response = send(body)
    data = json_body(response)
    errors = data.get("errors") or []
    if (errors or not response.ok) and not _refreshed and is_auth_error(response, errors):
        refresh()
        response = send(body)
        data = json_body(response)
        errors = data.get("errors") or []
    if errors:
        if is_auth_error(response, errors):
            raise ToolError("ab.gr refused the login. " + SETUP_HELP)
        raise ToolError(f"ab.gr: {error_text(errors)}")
    if not response.ok:
        raise ToolError(f"ab.gr returned HTTP {response.status_code}.")
    return data.get("data") or {}


# Queries. Field lists are cut down to what the tools return.

PRODUCT = (
    "code name manufacturerName manufacturerSubBrandName available purchasable previouslyBought "
    "productProposedPackaging maxOrderQuantity url stock{inStock} "
    "price{formattedValue value unit unitPriceFormatted supplementaryPriceLabel1 discountedPriceFormatted "
    "showStrikethroughPrice wasPrice} potentialPromotions{title}"
)
TIMESLOT = "slotCode dateFmt slotTimeRangeFmt cutOffTime deliveryType available"
CART = (
    "code guid totalUnitCount totalPrice{formattedValue value} remainingAmountToReachMov{formattedValue value} "
    "entries{entryNumber quantity product{code name manufacturerName productProposedPackaging price{unit}} "
    "totalPrice{formattedValue value}} "
    f"timeslot{{{TIMESLOT}}} "
    "deliveryAddress{formattedAddress streetName houseNumberName postalCode town}"
)
REFRESH = "mutation RefreshCustomerToken{refreshCustomerAuthCookies}"
CUSTOMER = "query CiamUser{CiamUser{name{first_name last_name}email{value}}}"
SEARCH = (
    "query GetProductSearch($lang:String $searchQuery:String $pageSize:Int $pageNumber:Int)"
    "{productSearch:productSearchV2(lang:$lang searchQuery:$searchQuery pageSize:$pageSize pageNumber:$pageNumber)"
    f"{{products{{{PRODUCT}}} pagination{{totalResults currentPage totalPages}}}}}}"
)
QUICK_SHOP = (
    "query groupedProductsForQuickShopOrders($orderCode:String! $searchQuery:String $pageSize:Int "
    "$pageNumber:Int $lang:String){groupedProductsForQuickShopOrders(orderCode:$orderCode "
    "searchQuery:$searchQuery pageSize:$pageSize pageNumber:$pageNumber lang:$lang)"
    f"{{groupedProducts{{name products{{{PRODUCT}}}}} pagination{{totalResults}}}}}}"
)
ORDERS = (
    "query MyOrdersGetOrders($getOrdersInput:GetOrdersInput!){getOrders:getOrdersV2(input:$getOrdersInput)"
    "{orders{code created status deliveryStatus totalItems totalPrice{formattedValue value} "
    "slotData{dateFmt slotTimeRangeFmt}}}}"
)
ORDER = (
    "query OrderDetails($code:String $lang:String $fields:String){order:getOrderDetailsV2(code:$code "
    "lang:$lang fields:$fields){code created status deliveryStatus statusDisplay paymentType cancellable "
    "totalPrice{formattedValue value} totalPriceFinal{formattedValue value} slotData{dateFmt slotTimeRangeFmt} "
    "entries{entryNumber quantity product{code name manufacturerName productProposedPackaging price{unit}} "
    "totalPrice{formattedValue value}}}}"
)
# The plain cart query returns zero totals until the cart is recalculated, so ask for that.
GET_CART = f"query getCartForCheckout($lang:String!){{cart:getCartV2(lang:$lang refreshCart:true){{{CART}}}}}"
UPDATE_CART = (
    "mutation MiniBasketUpdateCartEntryQuantity($lang:String! $entries:[EntryInput!]!)"
    "{cart:updateCartEntryQuantity(entries:$entries lang:$lang){totalQuantity "
    "cartModifications{productCode quantity quantityAdded statusCode}}}"
)
DELETE_ITEM = (
    "mutation DeleteItem($lang:String! $productCode:String){cart:deleteItemV2(lang:$lang "
    "productCode:$productCode){totalUnitCount}}"
)
SLOTS = (
    "query GetTimeSlotsV2($lang:String!){getTimeSlots:getTimeSlotsV2(lang:$lang){slots{date formattedDate "
    "dayUnavailable dayCapacityLimitReached dayTimeSlots{slotCode date dateFmt slotTimeRangeFmt startTime "
    "endTime cutOffTime deliveryType available disabled soonUnavailable localizedMessage "
    "preparationCost{name deliveryCost{formattedValue value}}}}}}"
)
SELECT_DELIVERY_MODE = (
    "mutation SelectDeliveryModeV2($deliveryTypeCode:String! $lang:String!){cart:selectDeliveryModeV2("
    "deliveryTypeCode:$deliveryTypeCode lang:$lang){dispatchType}}"
)
DISPATCH_TYPE = "query Cart($lang:String!){cart(lang:$lang){dispatchType}}"
SELECT_SLOT = (
    "mutation SelectTimeSlotV2($timeslotId:String! $lang:String!){cart:selectTimeSlotV2(timeslotId:$timeslotId "
    f"lang:$lang){{timeslot{{{TIMESLOT}}}}}}}"
)
VALIDATE = "mutation validateCartBeforeCheckout{result:validateCartBeforeCheckout{isValid reason}}"
MINIMUM = "query BasketMinimumAmount($lang:String!){cart:cartV2(lang:$lang){minimumSpendReached minimumSpendAmount}}"
CHECKOUT = (
    "query getCheckoutInfo($lang:String! $channel:String! $propositionType:String!){checkout(lang:$lang "
    "channel:$channel propositionType:$propositionType){paymentMethods{paymentMethods{delhaizePaymentTypeCode "
    "delhaizePaymentTypeName onlinePayment disabled}} cart{guid totalUnitCount "
    "totalPrice{formattedValue value} totalPriceFinal{formattedValue value} subTotal{formattedValue value} "
    "deliveryCosts{formattedValue value} preparationCosts{formattedValue value} bagsFee{formattedValue value} "
    f"timeslot{{{TIMESLOT}}} deliveryAddress{{streetName houseNumberName postalCode town}}}}}}}}"
)
PLACE_ORDER = (
    "mutation placeOrder($input:PlaceOrderInput! $channel:String! $lang:String $propositionType:String)"
    "{placeOrderV2(input:$input channel:$channel lang:$lang propositionType:$propositionType)"
    "{order reasonCode message errors{message reason}}}"
)
CANCEL_ORDER = (
    "mutation cancelOrder($code:String){cancelOrder:cancelOrderV2(code:$code){errors{message reason}reasonCode}}"
)


# Shaping API objects into tool output.

def product(item: dict) -> dict:
    price = item.get("price") or {}
    brand = " ".join(n for n in [item.get("manufacturerName"), item.get("manufacturerSubBrandName")] if n and n != "-")
    out = {
        "code": item.get("code"),
        "name": item.get("name"),
        "brand": brand or None,
        "price": price.get("discountedPriceFormatted") or price.get("formattedValue"),
        "unit_price": price.get("unitPriceFormatted") or price.get("supplementaryPriceLabel1"),
        "sold_by": price.get("unit"),
        "package": item.get("productProposedPackaging"),
        "in_stock": (item.get("stock") or {}).get("inStock", item.get("available")),
        "previously_bought": item.get("previouslyBought"),
    }
    if price.get("showStrikethroughPrice") and price.get("wasPrice"):
        out["was_price"] = price.get("wasPrice")
    promotions = [p.get("title") for p in item.get("potentialPromotions") or [] if p.get("title")]
    if promotions:
        out["promotions"] = promotions
    if item.get("maxOrderQuantity"):
        out["max_quantity"] = item.get("maxOrderQuantity")
    if item.get("url"):
        out["link"] = SITE + item["url"]
    return {key: value for key, value in out.items() if value is not None}


def entry(item: dict) -> dict:
    prod = item.get("product") or {}
    return {
        key: value
        for key, value in {
            "code": prod.get("code"),
            "name": prod.get("name"),
            "brand": prod.get("manufacturerName") if prod.get("manufacturerName") != "-" else None,
            "package": prod.get("productProposedPackaging"),
            "sold_by": (prod.get("price") or {}).get("unit"),
            "quantity": item.get("quantity"),
            "total": (item.get("totalPrice") or {}).get("formattedValue"),
        }.items()
        if value is not None
    }


def slot(item: dict | None) -> dict | None:
    if not item or not item.get("slotCode"):
        return None
    return {
        "slot_code": item.get("slotCode"),
        "date": item.get("dateFmt"),
        "time": item.get("slotTimeRangeFmt"),
        "order_by": item.get("cutOffTime"),
    }


def money(price: dict | None) -> str | None:
    return (price or {}).get("formattedValue")


def address(item: dict | None) -> str | None:
    item = item or {}
    if item.get("formattedAddress"):
        return item["formattedAddress"]
    street = " ".join(filter(None, [item.get("streetName"), item.get("houseNumberName")]))
    return ", ".join(filter(None, [street, " ".join(filter(None, [item.get("postalCode"), item.get("town")]))])) or None


def ensure_home_delivery() -> None:
    # A fresh cart has no delivery method, and ab.gr lists no slots until it has one.
    cart = gql("Cart", DISPATCH_TYPE, {"lang": LANG}).get("cart") or {}
    if cart.get("dispatchType") != "HOME_DELIVERY":
        gql("SelectDeliveryModeV2", SELECT_DELIVERY_MODE, {"deliveryTypeCode": "HOME_DELIVERY", "lang": LANG})


def get_cart() -> dict:
    return gql("getCartForCheckout", GET_CART, {"lang": LANG}).get("cart") or {}


def cart_summary(cart: dict) -> dict:
    out = {
        "items": [entry(e) for e in cart.get("entries") or []],
        "item_count": cart.get("totalUnitCount"),
        "total": money(cart.get("totalPrice")),
        "delivery_slot": slot(cart.get("timeslot")),
        "delivery_address": address(cart.get("deliveryAddress")),
    }
    remaining = cart.get("remainingAmountToReachMov") or {}
    if remaining.get("value"):
        out["short_of_minimum_by"] = remaining.get("formattedValue")
    return {key: value for key, value in out.items() if value is not None}


# Purchase history.

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").replace("ς", "σ")


def matches(query: str, *fields) -> bool:
    haystack = normalize(" ".join(f for f in fields if f))
    # Greek nouns change their endings (μπανάνα, μπανάνες), so match word stems.
    for word in normalize(query).split():
        stem = word if len(word) < 5 else word[: max(4, len(word) - 2)]
        if stem not in haystack:
            return False
    return True


def parse_date(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
    text = str(value).strip()
    if text.isdigit():
        return parse_date(int(text))
    for layout in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, layout).replace(tzinfo=STORE_TIMEZONE)
        except ValueError:
            pass
    if len(text) > 5 and text[-5] in "+-" and text[-4:].isdigit():
        text = text[:-2] + ":" + text[-2:]
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def list_orders(limit: int) -> list[dict]:
    orders, page, size = [], 0, min(limit, 20)
    while len(orders) < limit:
        variables = {
            "getOrdersInput": {"lang": LANG, "pageNo": page, "pageSize": size, "sort": "byDate", "fields": "FULL"}
        }
        batch = (gql("MyOrdersGetOrders", ORDERS, variables).get("getOrders") or {}).get("orders") or []
        orders.extend(batch)
        if len(batch) < size:
            break
        page += 1
    return orders[:limit]


def order_details(code: str) -> dict:
    return gql("OrderDetails", ORDER, {"code": code, "lang": LANG, "fields": "FULL"}).get("order") or {}


def load_cache() -> dict:
    try:
        cache = json.loads(CACHE_PATH.read_text())
        return cache if isinstance(cache, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def finished(order: dict) -> bool:
    status = str(order.get("status") or "").upper()
    return status in FINISHED_STATUSES or str(order.get("deliveryStatus") or "").upper() == "DELIVERED"


def history(months: int, max_fetch: int = 12) -> tuple[list[dict], bool]:
    """Return orders from the last `months` with their entries, and whether some are missing.

    Finished orders are cached. Open ones are fetched every time since they can change.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - months * 30.5 * 86400
    listed = [o for o in list_orders(200) if (parse_date(o.get("created")) or datetime.now(timezone.utc)).timestamp() >= cutoff]
    cache = load_cache()
    missing = [o["code"] for o in listed if o.get("code") and o["code"] not in cache]
    fetch, incomplete = missing[:max_fetch], len(missing) > max_fetch
    if fetch:
        with ThreadPoolExecutor(max_workers=6) as pool:
            fetched = dict(zip(fetch, pool.map(order_details, fetch)))
        for code, order in fetched.items():
            order["entries"] = [entry(e) for e in order.get("entries") or []]
            if finished(order):
                cache[code] = order
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False))
    else:
        fetched = {}
    orders = [cache.get(o["code"]) or fetched.get(o["code"]) for o in listed if o.get("code")]
    return [o for o in orders if o], incomplete


def product_stats(orders: list[dict]) -> dict[str, dict]:
    now = datetime.now(timezone.utc)
    stats: dict[str, dict] = {}
    for order in orders:
        if str(order.get("status") or "").upper() in CANCELLED_STATUSES:
            continue
        when = parse_date(order.get("created"))
        if not when:
            continue
        for item in order.get("entries") or []:
            if not item.get("code"):
                continue
            row = stats.setdefault(item["code"], {"item": item, "dates": [], "quantities": []})
            row["dates"].append(when)
            if isinstance(item.get("quantity"), (int, float)):
                row["quantities"].append(item["quantity"])
    out = {}
    for code, row in stats.items():
        dates = sorted(set(d.date() for d in row["dates"]))
        last = max(row["dates"])
        item = row["item"]
        result = {
            "code": code,
            "name": item.get("name"),
            "brand": item.get("brand"),
            "package": item.get("package"),
            "times_bought": len(dates),
            "last_bought": last.date().isoformat(),
            "days_since_last": (now - last).days,
            "usual_quantity": statistics.median(row["quantities"]) if row["quantities"] else None,
        }
        if len(dates) >= 2:
            gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
            result["every_days"] = round(statistics.median(gaps))
            # Due once it has been as long as the usual gap. Needs three purchases, two
            # are too few to tell a habit from a coincidence. Past three gaps the user
            # has most likely stopped buying it, so it's no longer due.
            regular = len(dates) >= 3
            since, every = result["days_since_last"], max(result["every_days"], 1)
            result["due"] = regular and every <= since < 3 * every
            if regular and since >= 3 * every:
                result["stopped"] = True
        out[code] = {key: value for key, value in result.items() if value is not None}
    return out


# Checkout.

def checkout_state() -> dict:
    """Collect what checkout_preview shows and what place_order checks again."""
    cart = get_cart()
    if not cart.get("entries"):
        return {"items": [], "problems": ["The cart is empty."]}
    # ab.gr reports some unfinished carts, like one without a slot, as errors. Those
    # become problems in the preview.
    errors, validation, checkout = [], {}, {}
    try:
        validation = gql("validateCartBeforeCheckout", VALIDATE).get("result") or {}
    except ToolError as error:
        errors.append(str(error).removeprefix("ab.gr: "))
    try:
        variables = {"lang": LANG, "channel": CHANNEL, "propositionType": PROPOSITION_TYPE}
        checkout = gql("getCheckoutInfo", CHECKOUT, variables).get("checkout") or {}
    except ToolError as error:
        errors.append(str(error).removeprefix("ab.gr: "))
    minimum = gql("BasketMinimumAmount", MINIMUM, {"lang": LANG}).get("cart") or {}
    totals = checkout.get("cart") or {}
    methods = ((checkout.get("paymentMethods") or {}).get("paymentMethods")) or []
    pay_on_delivery = [
        {"code": m.get("delhaizePaymentTypeCode"), "name": m.get("delhaizePaymentTypeName")}
        for m in methods
        if not m.get("onlinePayment") and not m.get("disabled") and m.get("delhaizePaymentTypeCode")
    ]
    items = [entry(e) for e in cart.get("entries") or []]
    chosen = slot(totals.get("timeslot") or cart.get("timeslot"))
    total = money(totals.get("totalPriceFinal")) or money(totals.get("totalPrice")) or money(cart.get("totalPrice"))
    problems = [f"ab.gr: {e}" for e in dict.fromkeys(errors) if "slotNotFound" not in e]
    if validation.get("isValid") is False:
        problems.append(f"ab.gr says the cart isn't ready: {validation.get('reason') or 'no reason given'}.")
    if minimum.get("minimumSpendReached") is False:
        problems.append(f"The cart is below the minimum order of {minimum.get('minimumSpendAmount')}.")
    if not chosen:
        problems.append("No delivery slot is chosen. Use delivery_slots and choose_slot.")
    if chosen and not checkout:
        problems.append("ab.gr returned no checkout details.")
    elif checkout and not pay_on_delivery:
        problems.append("ab.gr offers no pay-on-delivery method for this order.")
    fingerprint = json.dumps(
        [
            totals.get("guid") or cart.get("guid"),
            sorted((i.get("code"), i.get("quantity")) for i in items),
            total,
            (chosen or {}).get("slot_code"),
        ],
        ensure_ascii=False,
    )
    preview = {
        "items": items,
        "item_count": totals.get("totalUnitCount") or cart.get("totalUnitCount"),
        "subtotal": money(totals.get("subTotal")),
        "delivery_cost": money(totals.get("deliveryCosts")),
        "preparation_cost": money(totals.get("preparationCosts")),
        "bags_fee": money(totals.get("bagsFee")),
        "total": total,
        "delivery_slot": chosen,
        "delivery_address": address(totals.get("deliveryAddress") or cart.get("deliveryAddress")),
        "pay_on_delivery_methods": pay_on_delivery,
    }
    preview = {key: value for key, value in preview.items() if value not in (None, [])}
    if problems:
        preview["problems"] = problems
    else:
        preview["confirmation"] = digest(fingerprint)[:8]
    return preview


# Parameters and the tool runner.

def int_param(params: dict, name: str, default: int, low: int, high: int) -> int:
    value = params.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{name} must be a whole number.")
    return max(low, min(high, value))


def text_param(params: dict, name: str, required: bool = True) -> str | None:
    value = params.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ToolError(f"{name} is required.")
        return None
    if not isinstance(value, str):
        raise ToolError(f"{name} must be a string.")
    return value.strip()


def number_text(value: float) -> str:
    # The site sends cart quantity changes as strings, "2" rather than "2.0".
    return str(int(value)) if float(value).is_integer() else str(round(value, 3))


def run(main, login: bool = True) -> None:
    try:
        raw = sys.stdin.read()
        params = json.loads(raw) if raw.strip() else {}
        if not isinstance(params, dict):
            raise ToolError("Input must be a JSON object.")
        if login:
            load_cookies()
        try:
            result = main(params)
        finally:
            if login:
                save_cookies()
    except ToolError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print("Input is not valid JSON.", file=sys.stderr)
        sys.exit(1)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
