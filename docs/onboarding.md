# Onboarding

`pik check` is the setup doctor. It always checks the API key. With `--webhook-url` it also probes reachability, performs a signature round-trip, and checks TLS.

The signature round-trip registers a temporary endpoint, asks the API to sign a `webhook.ping`, verifies that signature locally with the secret just returned, and disables the endpoint. Delivery can fail because the URL is down and the signature check can still pass. Reachability is the check that requires the URL to answer.

## TLS certificate verification failed

**Symptom:** The TLS check is FAIL with `certificate verification failed`, `SSLCertVerificationError`, or a hostname mismatch. HTTP URLs are SKIP, not FAIL, with the detail "URL is not HTTPS".

**Signals:** SSLCertVerificationError, self-signed, expired certificate, hostname mismatch, not after, TLS, certificate verification failed.

**Fix:** Serve a certificate whose subject matches the webhook hostname and whose chain is trusted by the default trust store. Renew before `notAfter`. Use HTTPS in production. `--insecure` affects only the reachability probe; the TLS check still reports an untrusted certificate. Do not ship `--insecure` for a public endpoint.

## API base URL mistakes

**Symptom:** `pik check` says the API key check could not reach the API, or every call returns connection refused. A key that is valid on another host returns `invalid_api_key`.

**Signals:** connection refused on the API, wrong port, forgot /v1, base URL includes a path.

**Fix:** Point `--base-url` at the origin only, for example `http://127.0.0.1:8400`. Routes already include `/v1`. Confirm `GET /v1/health` returns `{"status":"ok"}` before debugging signatures.
