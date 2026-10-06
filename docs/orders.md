# Orders

Amounts are integers in minor currency units. `amount` is the sum of `quantity * unit_amount`. Currency is a lowercase ISO code such as `usd`. Status moves from `open` to `fulfilled` or `cancelled`.

`POST /v1/orders` emits `order.created`. Fulfill emits `order.fulfilled`. Cancel emits `order.cancelled`. Repeating the same transition returns the order and does not emit a second event.

## Order not found

**Symptom:** HTTP 404 `invalid_request_error` / `order_not_found`. The message is "No such order."

**Signals:** 404, unknown ord_ id, order id from another partner, order_not_found.

**Fix:** List orders with `GET /v1/orders` or poll `order.created` and use the `ord_` id from this partner. A missing order and an order owned by someone else both return 404 so ids are not an oracle. Check the base URL and the API key before assuming the id was deleted.

## Invalid order transition

**Symptom:** HTTP 409 `invalid_request_error` / `invalid_order_transition`. The message names the current status and the status you asked for, for example "Cannot change an order from fulfilled to cancelled."

**Signals:** 409, already fulfilled, already cancelled, invalid_order_transition, illegal state.

**Fix:** Read the order and branch on `status`. Only `open` orders can be fulfilled or cancelled. A second cancel of an already cancelled order is a no-op and returns 200. Fulfillment is final.

## Duplicate external id

**Symptom:** HTTP 409 `invalid_request_error` / `duplicate_external_id`. An order with this `external_id` already exists for the partner.

**Signals:** 409 duplicate_external_id, partner reference reused, unique constraint.

**Fix:** Look the original up by listing orders, or retry the create with the same `Idempotency-Key` and the same body so the stored response is replayed. Use a new `external_id` only when you mean to create a different order.

## Order validation failed

**Symptom:** HTTP 400 `invalid_request_error` / `validation_error`. `details` lists `loc`, `msg`, and `type` for each field.

**Signals:** 400 validation_error, empty items, quantity 0, uppercase currency, bad email, extra field rejected, Request validation failed.

**Fix:** Send at least one item, `quantity` >= 1, `unit_amount` >= 0, and a lowercase 3-letter currency. Customer email must look like `name@host.tld`. The schema rejects unknown fields (`extra` is forbidden). This error does not consume an Idempotency-Key.
