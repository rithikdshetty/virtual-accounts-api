"""
Local webhook receiver for end-to-end testing.

Run in a separate terminal:
    python tools/receiver.py <secret_from_webhook_endpoint_create>

It listens on http://localhost:9000/webhook and:
- Verifies the Webhook-Signature header
- Prints the event type and event id
- Returns 200 to ack

If signature verification fails it prints why and returns 400.
"""
import hashlib
import hmac
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


SECRET = None  # set from argv at startup
TOLERANCE_SECONDS = 300  # reject deliveries older than 5 minutes


def verify_signature(secret: str, signature_header: str, body: bytes) -> tuple[bool, str]:
    """
    Verify the Webhook-Signature header.
    Format: t=<unix_timestamp>,v1=<hex_hmac>
    """
    parts = {}
    for piece in signature_header.split(","):
        if "=" in piece:
            k, v = piece.split("=", 1)
            parts[k.strip()] = v.strip()

    if "t" not in parts or "v1" not in parts:
        return False, "malformed signature header"

    try:
        timestamp = int(parts["t"])
    except ValueError:
        return False, "invalid timestamp"

    age = abs(int(time.time()) - timestamp)
    if age > TOLERANCE_SECONDS:
        return False, f"timestamp too old: {age}s"

    expected_signed = f"{timestamp}.{body.decode('utf-8')}"
    expected_sig = hmac.new(
        secret.encode("utf-8"), expected_signed.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, parts["v1"]):
        return False, "signature mismatch"

    return True, "ok"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        signature = self.headers.get("Webhook-Signature", "")

        ok, reason = verify_signature(SECRET, signature, body)
        if not ok:
            print(f"REJECTED: {reason}")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"signature invalid")
            return

        try:
            payload = json.loads(body)
            print(f"DELIVERED: type={payload.get('type')} id={payload.get('id')}")
        except json.JSONDecodeError:
            print("DELIVERED but body wasn't JSON")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        # Suppress default per-request logging
        pass


def main():
    global SECRET
    if len(sys.argv) != 2:
        print("Usage: python tools/receiver.py <webhook_secret>")
        sys.exit(1)
    SECRET = sys.argv[1]
    server = HTTPServer(("127.0.0.1", 9000), Handler)
    print(f"Receiver listening on http://localhost:9000/webhook")
    print(f"Secret: {SECRET[:20]}...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nReceiver stopped.")


if __name__ == "__main__":
    main()
