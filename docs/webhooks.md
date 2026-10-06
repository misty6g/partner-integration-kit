# Webhooks

Register an endpoint with `POST /v1/webhook_endpoints`. The response includes `secret` once (`whsec_...`). Later reads return only `secret_hint`.

Deliveries are rows the worker claims. Each attempt signs the exact stored body. The header is:

```
Pik-Signature: t=<unix>,v1=<hex hmac sha256>
```

The signed string is `{timestamp}.{raw_body}`. Also sent: `Pik-Delivery`, `Pik-Event-Id`, `Pik-Event-Type`. Receivers should verify with `pik_sdk.webhooks.verify` or `verifyWebhook` in the TypeScript helper, using a tolerance of 300 seconds, and then respond 2xx.

Status values: `pending`, `in_flight`, `succeeded`, `failed`, `dead_letter`. Failures use exponential backoff: base, then double, capped by `PIK_WEBHOOK_BACKOFF_CAP_SECONDS`. The default budget is 5 attempts (`PIK_WEBHOOK_MAX_ATTEMPTS`). A non-2xx response and a connection error are both failures. Redirects are not followed.

`webhook.ping` is sent only to the endpoint you ping, even when that name is not in `events`.

## Signature mismatch

**Symptom:** The receiver's verifier raises `signature_mismatch`. The HMAC over `{timestamp}.{raw_body}` does not equal `v1`.

**Signals:** signature_mismatch, HMAC mismatch, wrong secret, body was parsed and re-serialized, v1 digest differs.

**Fix:** Verify the raw request bytes before JSON parsing. Use the endpoint secret from create time, not the API key and not `secret_hint`. A pretty-printed or reordered body will not match. Compare with `hmac.compare_digest` or `timingSafeEqual`.

## Timestamp outside tolerance

**Symptom:** The verifier raises `timestamp_expired`. The absolute difference between now and `t` is greater than the tolerance, default 300 seconds.

**Signals:** timestamp_expired, replay window, clock skew, stale webhook, five minutes, tolerance.

**Fix:** Sync the receiver's clock with NTP. Pass the same tolerance the sender uses (300 seconds unless you changed it). Do not accept an old `t` to "make retries work"; the sender puts a fresh timestamp on every attempt. Store `Pik-Delivery` to deduplicate retries.

## Malformed signature header

**Symptom:** The verifier raises `malformed_signature`. The header is missing, has no `t` or `v1`, the timestamp is not an integer, or `v1` is not 64 hex characters.

**Signals:** malformed_signature, missing Pik-Signature, empty header, truncated digest.

**Fix:** Read the `Pik-Signature` header unchanged. The form is `t=1700000000,v1=<64 hex chars>`. A proxy that strips unknown headers will cause this. Log the header names you received before you rotate secrets.

## Dead-letter deliveries

**Symptom:** `GET /v1/webhook_deliveries?status=dead_letter` shows rows whose `attempt_count` reached the max. `last_error` is `HTTP 500: ...`, a timeout, or `endpoint_disabled`.

**Signals:** dead_letter, attempt_count 5, retries exhausted, DLQ, last_error.

**Fix:** Fix the receiver so it returns 2xx for a valid signature, then call `POST /v1/webhook_deliveries/{id}/replay`. Replay sets status back to `pending` and zeroes `attempt_count`. Disabling an endpoint dead-letters queued attempts with `endpoint_disabled`; enable it before replaying.

## Replaying a delivery

**Symptom:** HTTP 409 `delivery_in_flight` if you replay an attempt the worker has claimed. A successful replay response has `status` `pending` and `attempt_count` 0.

**Signals:** delivery_in_flight, replay endpoint, requeue, 409 in flight.

**Fix:** Replay `failed` and `dead_letter` rows after the receiver is healthy. Wait until an `in_flight` row finishes. The worker must be running (`python -m pik_api.worker`) or the row will sit in `pending`. Replay does not itself perform the HTTP call.

## Webhook endpoint unreachable

**Symptom:** `pik check` reports reachability FAIL with `ConnectError` or a timeout. Deliveries show `last_error` starting with `ConnectError` or `ReadTimeout`. Create endpoint returns `invalid_webhook_url` or `webhook_url_blocked` for a bad URL.

**Signals:** connection refused, ConnectError, unreachable, timeout, invalid_webhook_url, webhook_url_blocked, link-local, metadata host.

**Fix:** Expose an HTTP server that accepts POST and returns 2xx. The URL must be absolute `http` or `https` with no username or password. Link-local and cloud metadata hosts are rejected. Loopback is allowed for local receivers. From Docker, `127.0.0.1` is the container, not your laptop; use a hostname the worker can resolve.

## Endpoint not found

**Symptom:** HTTP 404 `endpoint_not_found` for a `whe_` id this partner does not own. Ping on a disabled endpoint returns 409 with the same code and tells you to enable it.

**Signals:** endpoint_not_found, deleted webhook, disabled ping.

**Fix:** List endpoints with `GET /v1/webhook_endpoints`. `DELETE` disables the row; it does not remove the id. PATCH `status` to `active` before pinging again.
