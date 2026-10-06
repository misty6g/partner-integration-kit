# Pagination

List endpoints use cursor pagination. `limit` defaults to 20 and cannot exceed 100. Results are ordered by `created_at` descending, then `id` descending. When `has_more` is true, pass the returned `next_cursor` as `starting_after`. The cursor is the last object id on the page, not an offset.

## Invalid pagination cursor

**Symptom:** HTTP 400 `invalid_request_error` / `invalid_cursor`. The message says `starting_after` does not match a resource for this partner.

**Signals:** invalid_cursor, starting_after, unknown cursor, cursor from another partner, 400 pagination.

**Fix:** Use `next_cursor` from the previous response of the same list endpoint and the same API key. Do not pass an order id to `GET /v1/events`. A cursor is not a page number. If you filtered by `status` or `type`, keep that filter stable while you walk the cursor; the cursor itself is only the object id.
