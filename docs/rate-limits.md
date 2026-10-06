# Rate limits

Each API key has a fixed window. The default is 120 requests per 60 seconds. `GET /v1/account` reports `rate_limit` and `rate_window_seconds`. Successful responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`.

The counter lives in SQLite. Set `PIK_REDIS_URL` to move the same window onto Redis. Health checks are not authenticated and are not counted.

## Rate limit exceeded

**Symptom:** HTTP 429 `rate_limit_error` / `rate_limit_exceeded`. The body says "Too many requests." The `Retry-After` header is the number of seconds until the window resets. Remaining is 0.

**Signals:** 429, Retry-After, X-RateLimit-Remaining 0, too many requests, burst, rate_limit_exceeded.

**Fix:** Honor `Retry-After` and stop sending until `X-RateLimit-Reset`. Do not retry immediately in a tight loop; that keeps the window full. Spread webhook reconciliation and backfills. For a local load test, raise `PIK_RATE_LIMIT`. Idempotency-Key does not bypass the limiter, and a 429 is not stored as the idempotent response.
