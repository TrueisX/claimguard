"""Run the review-api Lambda handler as an ordinary web server, for Docker and Kubernetes.

Each HTTP request is turned into the same event API Gateway sends to the Lambda, the unchanged
handler.lambda_handler answers it, and the reply is sent back. No website or API code is copied
or rewritten, so the container behaves exactly like the Lambda.
"""
import base64
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handler  # noqa: E402  (needs TABLE and the AWS settings from the environment)

MAX_BODY = 1_000_000


class Context:
    function_name = "review-api-container"
    aws_request_id = "container"


class Server(BaseHTTPRequestHandler):
    server_version = "ClaimGuard"
    protocol_version = "HTTP/1.1"

    def send(self, status, headers, data):
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def run(self):
        parts = urlsplit(self.path)
        if parts.path == "/healthz":                       # for Docker and Kubernetes probes; touches no AWS service
            return self.send(200, {"Content-Type": "text/plain"}, b"ok")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self.send(413, {"Content-Type": "application/json"}, b'{"error": "body too large"}')
        raw = self.rfile.read(length) if length else b""
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        event = {
            "version": "2.0",
            "rawPath": parts.path,
            "rawQueryString": parts.query,
            "queryStringParameters": query or None,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "requestContext": {"http": {"method": self.command, "path": parts.path}},
            "body": raw.decode("utf-8", "replace") if raw else None,
            "isBase64Encoded": False,
        }
        try:
            result = handler.lambda_handler(event, Context())
        except Exception:                                  # log the detail, never show it to the caller
            traceback.print_exc()
            return self.send(500, {"Content-Type": "application/json"}, b'{"error": "internal error"}')
        body = result.get("body", "")
        if result.get("isBase64Encoded"):
            data = base64.b64decode(body)
        else:
            data = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        self.send(result.get("statusCode", 200), result.get("headers", {}), data)

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_OPTIONS = run


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"ClaimGuard web server on port {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Server).serve_forever()
