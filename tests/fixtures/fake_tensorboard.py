"""Owned GPU-free service fixture; does not claim TensorBoard execution."""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

port, prefix = int(sys.argv[1]), sys.argv[2]
time.sleep(0.4)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != prefix + "/data/environment":
            self.send_error(404)
            return
        data = json.dumps({"fixture": "owned fake TensorBoard process"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
