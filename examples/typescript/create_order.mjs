/**
 * Create an order and verify a signed webhook.ping.
 *
 * Build the generated client first:
 *   npm install && npm run build
 *   (from sdk/typescript/generated)
 *
 * Then, with the API listening:
 *   node --experimental-strip-types examples/typescript/create_order.mjs
 */
import { createRequire } from "node:module";
import { createServer } from "node:http";

import { verifyWebhook } from "../../sdk/typescript/src/webhooks.ts";

const require = createRequire(import.meta.url);
const generated = require("../../sdk/typescript/generated/dist/index.js");

const host = process.env.PIK_BASE_URL ?? "http://127.0.0.1:8400";
const apiKey = process.env.PIK_API_KEY ?? "pk_test_acme_7f3a9c2e1b84d0";
const config = new generated.Configuration({ basePath: host, apiKey });

const secret = { value: "" };
const seen = { body: null };

const server = createServer((request, response) => {
  const chunks = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => {
    const body = Buffer.concat(chunks);
    try {
      verifyWebhook(body, request.headers["pik-signature"] ?? "", secret.value);
      seen.body = body;
      response.writeHead(204);
      response.end();
    } catch (error) {
      response.writeHead(400);
      response.end(String(error));
    }
  });
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
const webhookUrl = `http://127.0.0.1:${address.port}/hooks`;

try {
  const accountApi = new generated.AccountApi(config);
  const orders = new generated.OrdersApi(config);
  const hooks = new generated.WebhooksApi(config);
  const account = await accountApi.getAccount();
  console.log(`partner ${account.name} (${account.id})`);
  const order = await orders.createOrder({
    idempotencyKey: "example-ts-1001",
    orderCreate: {
      currency: "usd",
      externalId: "example-ts-1001",
      customer: { name: "Ada Lovelace", email: "ada@analytical.example" },
      items: [{ sku: "WIDGET-1", quantity: 2, unitAmount: 2500 }],
    },
  });
  console.log(`created ${order.id} amount=${order.amount} status=${order.status}`);
  const endpoint = await hooks.createWebhookEndpoint({
    webhookEndpointCreate: { url: webhookUrl, events: ["order.created", "order.cancelled"] },
  });
  secret.value = endpoint.secret;
  const ping = await hooks.pingWebhookEndpoint({ endpointId: endpoint.id });
  verifyWebhook(Buffer.from(ping.body), ping.signature, endpoint.secret);
  console.log(`verified ${ping.deliveryId} delivery=${ping.deliveryStatus}`);
  await hooks.disableWebhookEndpoint({ endpointId: endpoint.id });
} finally {
  server.close();
}

if (!seen.body) {
  throw new Error("webhook receiver did not see the ping");
}
console.log("receiver accepted the signed ping");
