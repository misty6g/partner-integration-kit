# Error index

Partner errors use one JSON object:

```json
{
  "error": {
    "type": "invalid_request_error",
    "code": "order_not_found",
    "message": "No such order.",
    "doc_url": "https://github.com/misty6g/partner-integration-kit/blob/main/docs/orders.md#order-not-found",
    "request_id": "req_..."
  }
}
```

`type` is `authentication_error`, `rate_limit_error`, `idempotency_error`, or `invalid_request_error`. `code` is stable. `doc_url` points at the runbook section. `X-Request-Id` echoes the caller value when it is a short token, otherwise the server generates one.

## How to use this runbook

**Symptom:** You have an error payload and want the matching section.

**Fix:** Paste the HTTP status, the `code`, and the `message` into `pik help`. With no LLM key the command returns the best matching section from these documents (retrieval mode). Set `PIK_LLM_API_KEY` or `OPENAI_API_KEY` to have a model rewrite that section; if the model call fails, the same retrieved fix is returned.
