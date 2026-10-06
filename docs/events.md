# Events

`GET /v1/events` is the reconciliation feed when a webhook is missed. Platform events use `order.created`, `order.fulfilled`, `order.cancelled`, and `webhook.ping`. Partners may publish their own types with `POST /v1/events`. Those events are stored and fanned out to subscribed endpoints.

## Reserved event type

**Symptom:** HTTP 400 `invalid_request_error` / `reserved_event_type` when publishing a type that starts with `order.` or `webhook.`.

**Signals:** reserved_event_type, cannot publish order.created, webhook.ping rejected on POST /v1/events.

**Fix:** Use a partner namespace such as `inventory.updated` or `invoice.paid`. The platform is the only publisher of `order.*` and `webhook.*`. Subscribe to those names on a webhook endpoint instead of posting them.

## Invalid event type filter

**Symptom:** HTTP 400 `invalid_request_error` / `invalid_event_type`. The list filter contains characters other than lowercase letters, digits, dot, and underscore.

**Signals:** invalid_event_type, GET /v1/events?type=Order.Created, bad type query.

**Fix:** Filter with the exact published name, for example `type=order.created`. An unknown but well-formed type returns an empty page, not an error. Fix the spelling if the page is empty and you expected rows.

## Payload too large

**Symptom:** HTTP 400 `invalid_request_error` / `payload_too_large`. Event `data` is larger than 64 KiB.

**Signals:** payload_too_large, 65536, huge JSON body on POST /v1/events.

**Fix:** Store the bulky document on your side and send an id plus a short summary in `data`. The webhook body is the event envelope, so a large payload is copied into every delivery.

## Event not found

**Symptom:** HTTP 404 `invalid_request_error` / `event_not_found`.

**Signals:** 404 event_not_found, evt_ id from another partner.

**Fix:** Copy the id from `GET /v1/events` or from the `Pik-Event-Id` webhook header for this partner. Foreign ids return 404.
