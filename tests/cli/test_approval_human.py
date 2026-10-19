"""The installed command refuses automation before sending any HTTP request."""

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cli_support import LW, ROOT


def test_json_and_non_tty_approval_never_contact_the_gateway(lab):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(("GET", self.path))
            self.send_error(500)

        def do_POST(self):
            calls.append(("POST", self.path))
            self.send_error(500)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        environment = lab[2] | {"LW_URL": f"http://127.0.0.1:{server.server_port}"}
        for flags in ((), ("--json",), ("--deny", "--json")):
            process = subprocess.run(
                [str(LW), "approve", "inv-do-not-send", *flags],
                cwd=ROOT,
                env=environment,
                input="approve\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            assert process.returncode == 1
            if "--json" in flags:
                assert json.loads(process.stdout)["error"]["detail"]["code"] == "approval_requires_human"
            else:
                assert "approval_requires_human" in process.stderr
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
