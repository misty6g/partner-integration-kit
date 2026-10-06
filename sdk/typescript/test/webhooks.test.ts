import assert from "node:assert/strict";
import test from "node:test";

import {
  MALFORMED,
  MISMATCH,
  TIMESTAMP_EXPIRED,
  WebhookSignatureError,
  signWebhook,
  verifyWebhook,
} from "../src/webhooks.ts";

const payload = Buffer.from('{"id":"evt_test","type":"webhook.ping"}');
const secret = "whsec_test_secret";
const timestamp = 1700000000;
const header =
  "t=1700000000,v1=14f56858cb772a58ae166d4bb7989b72117a5b8cfcb1b2ee7ba3d58b23a77cfd";

test("known vector matches the Python signer", () => {
  assert.equal(signWebhook(payload, secret, timestamp), header);
  verifyWebhook(payload, header, secret, { now: timestamp });
});

test("rejects a stale timestamp", () => {
  assert.throws(
    () => verifyWebhook(payload, header, secret, { now: timestamp + 301 }),
    (error: unknown) => error instanceof WebhookSignatureError && error.code === TIMESTAMP_EXPIRED,
  );
});

test("rejects a tampered body", () => {
  assert.throws(
    () => verifyWebhook(Buffer.from('{"id":"evt_other"}'), header, secret, { now: timestamp }),
    (error: unknown) => error instanceof WebhookSignatureError && error.code === MISMATCH,
  );
});

test("rejects a malformed header", () => {
  assert.throws(
    () => verifyWebhook(payload, "t=1700000000", secret, { now: timestamp }),
    (error: unknown) => error instanceof WebhookSignatureError && error.code === MALFORMED,
  );
});
