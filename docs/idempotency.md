# Idempotency

Unsafe routes accept `Idempotency-Key`: create order, cancel, fulfill, create event, create webhook endpoint, and replay delivery. The key is scoped to the partner. The server hashes the method, the path, and the raw body.

The same key and the same request replay the stored status and body and set `Idempotent-Replayed: true`. A 429 is not stored. Validation errors that FastAPI raises before the handler runs do not consume the key.

## Invalid Idempotency key

**Symptom:** HTTP 400 `invalid_request_error` / `invalid_idempotency_key`.

**Signals:** spaces in the key, longer than 200 characters, characters other than letters, digits, underscore, hyphen, colon, or dot.

**Fix:** Send a key of 1 to 200 characters matching `[A-Za-z0-9_.:-]`. UUIDs and stripe-style tokens are safe. Do not use the order's external_id unless you want those two concepts tied together.

## Idempotency key conflict

**Symptom:** HTTP 409 `idempotency_error` / `idempotency_key_conflict`. The message says the key was already used with a different request.

**Signals:** same Idempotency-Key, different JSON body, 409 idempotency_key_conflict, hash mismatch.

**Fix:** Generate a new key when the payload changes. Reuse a key only to retry the identical request, including field order in the raw body. A conflict is not an order failure; no second order was created by this call.

## Idempotency request in progress

**Symptom:** HTTP 409 `idempotency_error` / `idempotency_in_progress`. Another call with this key has reserved the record and has not stored a response yet.

**Signals:** parallel retries, timeout then immediate retry, 409 idempotency_in_progress, state processing.

**Fix:** Wait for the original request to finish, or retry it once, before sending another. Do not fan out the same key across workers. If the original client timed out, a later retry with the same body will replay the stored response after the first call completes.
