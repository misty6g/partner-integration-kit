import { createHmac, timingSafeEqual } from "node:crypto";

export const TIMESTAMP_EXPIRED = "timestamp_expired";
export const MISMATCH = "signature_mismatch";
export const MALFORMED = "malformed_signature";

export class WebhookSignatureError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "WebhookSignatureError";
    this.code = code;
  }
}

export function signWebhook(payload: Buffer | string, secret: string, timestamp: number): string {
  const body = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
  const digest = digestFor(body, secret, timestamp);
  return `t=${timestamp},v1=${digest}`;
}

export function verifyWebhook(
  payload: Buffer | string,
  header: string,
  secret: string,
  options: { toleranceSeconds?: number; now?: number } = {},
): void {
  const body = Buffer.isBuffer(payload) ? payload : Buffer.from(payload);
  const tolerance = options.toleranceSeconds ?? 300;
  if (tolerance < 0) {
    throw new Error("toleranceSeconds must be >= 0");
  }
  const { timestamp, signature } = parseHeader(header);
  const now = options.now ?? Math.floor(Date.now() / 1000);
  if (Math.abs(now - timestamp) > tolerance) {
    throw new WebhookSignatureError(
      TIMESTAMP_EXPIRED,
      `Signature timestamp ${timestamp} is outside the ${tolerance}s replay window.`,
    );
  }
  const expected = digestFor(body, secret, timestamp);
  const left = Buffer.from(expected);
  const right = Buffer.from(signature);
  if (left.length !== right.length || !timingSafeEqual(left, right)) {
    throw new WebhookSignatureError(MISMATCH, "Webhook signature did not match the payload and secret.");
  }
}

function digestFor(payload: Buffer, secret: string, timestamp: number): string {
  return createHmac("sha256", secret).update(`${timestamp}.`).update(payload).digest("hex");
}

function parseHeader(header: string): { timestamp: number; signature: string } {
  if (!header || typeof header !== "string") {
    throw new WebhookSignatureError(MALFORMED, "Missing Pik-Signature header.");
  }
  let timestamp: number | null = null;
  let signature: string | null = null;
  for (const part of header.split(",")) {
    const piece = part.trim();
    const index = piece.indexOf("=");
    if (index === -1) {
      throw new WebhookSignatureError(MALFORMED, "Malformed Pik-Signature header.");
    }
    const key = piece.slice(0, index).trim();
    const value = piece.slice(index + 1).trim();
    if (key === "t") {
      if (!/^\d+$/.test(value)) {
        throw new WebhookSignatureError(MALFORMED, "Signature timestamp is not an integer.");
      }
      timestamp = Number(value);
    } else if (key === "v1") {
      if (!/^[0-9a-fA-F]{64}$/.test(value)) {
        throw new WebhookSignatureError(MALFORMED, "Signature v1 digest is not 64 hex characters.");
      }
      signature = value.toLowerCase();
    }
  }
  if (timestamp === null || signature === null) {
    throw new WebhookSignatureError(MALFORMED, "Pik-Signature must include t and v1.");
  }
  return { timestamp, signature };
}
