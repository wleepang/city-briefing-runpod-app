#!/opt/venv/bin/python
"""Stands in for llama-server: 503 on /health for 3s, then 200; answers chat requests, streamed or not."""
import http.server, json, sys, time

args = sys.argv[1:]
port = int(args[len(args) - 1 - args[::-1].index("--port") + 1])  # the last one wins, as in llama-server
print("stub started with:", " ".join(args), flush=True)
t0 = time.time()


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            ready = time.time() - t0 > 3
            return self._send(200 if ready else 503, {"status": "ok" if ready else "loading"})
        if self.path == "/v1/models":
            return self._send(200, {"data": [{"id": "stub-model"}]})
        self._send(404, {"error": "nope"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("bad"):
            return self._send(400, {"error": {"message": "bad request"}})
        if body.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            for piece in ["Hel", "lo ", "there"]:
                chunk = ("data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n").encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk)); self.wfile.flush(); time.sleep(0.2)
            end = b"data: [DONE]\n\n"
            self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(end), end))
            return
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": "Hello there"}}]})

    def log_message(self, *a): pass


http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
