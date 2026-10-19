# AB Vassilopoulos plugin

Lets Stavrobot do your grocery shopping at [AB Vassilopoulos](https://www.ab.gr). It learns what you usually buy from your past orders, fills the cart, books a delivery slot and places the order, paid on delivery.

ab.gr has no public API. The plugin uses the same GraphQL endpoint the website uses, so a site update can break it without warning.

## Installation

Tell Stavrobot to install `https://github.com/vmasto/stavrobot-plugin-ab`.

## Configuration

- `cookie`: the Cookie header of a logged-in ab.gr browser session. ab.gr's login needs a captcha, so the plugin can't log in by itself. To get the cookie:
  1. Log in at https://www.ab.gr in a desktop browser.
  2. Open the developer tools on the Network tab and reload the page.
  3. Click any request to `www.ab.gr/api/v1/` and copy the value of its `Cookie` request header.
  4. Paste it into the `cookie` setting on `/settings/plugins`. Don't paste it into the chat, it gives full access to your ab.gr account.

The plugin saves the cookies ab.gr renews, so the login should keep working. If ab.gr logs it out, the tools say so and you paste a new cookie the same way.

`place_order` places real orders, paid on delivery by card or cash. The bot is told to show you a preview and wait for your yes, and the order fails if the cart, total or slot changed since that preview. If you'd rather place orders yourself, untick `place_order` under the plugin's permissions in `/settings/plugins`.

## How it learns what you buy

`purchase_history` reads your past orders and works out, per product, how many times you bought it, how much you usually get and how many days usually pass between purchases. A product you bought at least three times is `due` once that gap has passed, so the bot can remind you of things you forgot. Finished orders are cached in `order_cache.json` in the plugin directory, so only new orders are fetched.

`my_products` is the quick shop list on ab.gr, everything from your recent orders, with the same purchase counts. When you ask for "bananas", the bot looks there first and picks the bananas you actually buy.

## Tools

- `account`: check the login.
- `search`: search the catalog.
- `my_products`: products from your recent orders.
- `purchase_history`: what you buy, how often, and what's due.
- `orders`: list orders, or show one order's items.
- `cart`: show the cart.
- `update_cart`: set product quantities, 0 removes a product.
- `delivery_slots`: list free delivery slots.
- `choose_slot`: reserve a slot.
- `checkout_preview`: items, fees, total, slot, address and payment options, with a confirmation code.
- `place_order`: place the previewed order, paid on delivery.
- `cancel_order`: cancel an order that isn't being prepared yet.

## Tests

```bash
uv run --with pytest --with requests pytest tests
```

The tests run against a fake API, no account needed.
