"""Create an order and verify a signed webhook.ping with the generated client."""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pik_client
from pik_client import AccountApi, OrdersApi, WebhooksApi
from pik_client.models.customer_in import CustomerIn
from pik_client.models.order_create import OrderCreate
from pik_client.models.order_item_in import OrderItemIn
from pik_client.models.webhook_endpoint_create import WebhookEndpointCreate
from pik_sdk.webhooks import verify

HOST = os.environ.get("PIK_BASE_URL", "http://127.0.0.1:8400")
API_KEY = os.environ.get("PIK_API_KEY", "pk_test_acme_7f3a9c2e1b84d0")


def main() -> None:
    received: dict[str, bytes] = {}
    secret = {"value": ""}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            header = self.headers.get("Pik-Signature", "")
            verify(body, header, secret["value"])
            received["body"] = body
            self.send_response(204)
            self.end_headers()

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    webhook_url = f"http://127.0.0.1:{server.server_address[1]}/hooks"
    configuration = pik_client.Configuration(host=HOST)
    configuration.api_key["ApiKeyAuth"] = API_KEY
    try:
        with pik_client.ApiClient(configuration) as client:
            account = AccountApi(client).get_account()
            print(f"partner {account.name} ({account.id})")
            order = OrdersApi(client).create_order(
                OrderCreate(
                    currency="usd",
                    external_id="example-1001",
                    customer=CustomerIn(name="Ada Lovelace", email="ada@analytical.example"),
                    items=[OrderItemIn(sku="WIDGET-1", quantity=2, unit_amount=2500)],
                ),
                idempotency_key="example-create-1001",
            )
            print(f"created {order.id} amount={order.amount} status={order.status}")
            endpoint = WebhooksApi(client).create_webhook_endpoint(
                WebhookEndpointCreate(url=webhook_url, events=["order.created", "order.cancelled"])
            )
            secret["value"] = endpoint.secret
            ping = WebhooksApi(client).ping_webhook_endpoint(endpoint.id)
            verify(ping.body.encode("utf-8"), ping.signature, endpoint.secret)
            print(f"verified {ping.delivery_id} delivery={ping.delivery_status}")
            WebhooksApi(client).disable_webhook_endpoint(endpoint.id)
    finally:
        server.shutdown()
    if "body" not in received:
        raise SystemExit("webhook receiver did not see the ping")
    print("receiver accepted the signed ping")


if __name__ == "__main__":
    main()
