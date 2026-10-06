# Authentication

Partner requests send a secret key in the `X-API-Key` header. Demo keys are created on startup when `PIK_SEED_DEMO=1`. Keys are stored as SHA-256 hashes. A prefix is all the API returns after creation.

## Missing API key

**Symptom:** HTTP 401 `authentication_error` / `missing_api_key`. The message is "Send the partner API key in the X-API-Key header."

**Signals:** no header, empty X-API-Key, 401 missing_api_key.

**Fix:** Add the header `X-API-Key: pk_test_...` on every call except `GET /v1/health` and `GET /v1/ready`. The onboarding CLI passes it as `pik check --api-key`.

## Invalid API key

**Symptom:** HTTP 401 `authentication_error` / `invalid_api_key`. The message is "The API key was not recognized."

**Signals:** unrecognized key, wrong environment, copied a prefix instead of the full secret, 401 invalid_api_key.

**Fix:** Use the full secret that was issued for this environment. The account endpoint only echoes `api_key_prefix`, so a prefix cannot be replayed as a credential. Confirm you are calling the base URL that issued the key (`pik check --base-url`).

## Revoked API key

**Symptom:** HTTP 401 `authentication_error` / `revoked_api_key`. The message says the key has been revoked.

**Signals:** revoked_at, rotated credential, 401 revoked_api_key.

**Fix:** Issue a new key for the partner and update the integration. A revoked key fails before rate limiting. Old webhook signing secrets are independent of the API key and are not revoked by this error.
