"""Exercise real MLX provider and runtime health against an isolated HTTP peer; no inference."""
import argparse
import json
import os
import socket
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    requests, errors = [], []

    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append({"path": self.path, "method": "GET"})
            assert self.path == "/v1/models"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": str(Path.home() / "Library/Application Support/Squipi/mlx-model")}]}).encode())

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append({"path": self.path, "method": "POST", "body": body})
                assert self.path == "/v1/chat/completions"
                assert body["model"] == "default_model"
                assert self.headers["Authorization"] == "Bearer local"
                prompt = body["messages"][-1]["content"]
                self.send_response(200)
                if prompt == "Hi":
                    assert body["stream"] is False and body["max_tokens"] == 1
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"choices": [{"message": {"content": "Hi"}, "finish_reason": "length"}]}).encode())
                    return
                assert body["stream"] is True
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if prompt == "COMPLETE":
                    delta = {"content": "ready"}
                    finish = "stop"
                else:
                    assert prompt in ["TRUNCATED", "EOF"]
                    delta = {"tool_calls": [{"index": 0, "id": "fixture", "function": {"name": "profile_dataset", "arguments": "{}"}}]}
                    finish = "length" if prompt == "TRUNCATED" else None
                self.wfile.write(("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": None}]}) + "\n\n").encode())
                if finish:
                    self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
            except Exception as error:
                errors.append(repr(error))
                self.send_response(400)
                self.end_headers()

    class DeadPeer(Peer):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "method": "POST", "body": body, "dead_external": True})
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"generation thread died"}')

    with tempfile.TemporaryDirectory(prefix="squipi-mlx-health-") as temp:
        executable = Path(temp) / "checks"
        sources = ["CloudApprovalLedger.swift", "MLXConfiguration.swift", "NebiusModels.swift", "Providers.swift", "MLXProvider.swift"]
        compile_result = subprocess.run([
            os.environ.get("SQUIPI_SWIFTC", "swiftc"), "-swift-version", "5", "-parse-as-library", "-module-cache-path", str(Path(temp) / "module-cache"),
            *[str(repo / "Sources" / "Squipi" / source) for source in sources],
            str(repo / "tests" / "mlx_health_checks.swift"), "-o", str(executable)
        ], capture_output=True, text=True, timeout=180)
        (args.output / "compile.log").write_text(compile_result.stdout + compile_result.stderr)
        assert compile_result.returncode == 0, compile_result.stderr
        server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        dead_server = ThreadingHTTPServer(("127.0.0.1", 0), DeadPeer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=dead_server.serve_forever, daemon=True).start()
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            closed_port = reserve.getsockname()[1]
        try:
            result = subprocess.run([str(executable), f"http://127.0.0.1:{server.server_port}", temp,
                                     f"http://127.0.0.1:{closed_port}", f"http://127.0.0.1:{dead_server.server_port}"],
                                    capture_output=True, text=True, timeout=60)
            (args.output / "native.log").write_text(result.stdout + result.stderr)
            (args.output / "requests.json").write_text(json.dumps(requests, indent=2) + "\n")
            catalog_reads = sum(request["method"] == "GET" for request in requests)
            passed = result.returncode == 0 and not errors and len(requests) == 11 and catalog_reads == 6
            (args.output / "verification.json").write_text(json.dumps({
                "passed": passed, "controlled_http_peer": True, "live_model_or_gpu": False,
                "request_count": len(requests), "catalog_read_count": catalog_reads, "peer_errors": errors,
                "swift_exit_code": result.returncode, "stdout": result.stdout,
                "scope": "Actual MLX provider EOF rejection and runtime unhealthy transition; current vs prior crash logs and external recovery; stale crash with closed endpoint reaches fresh startup; reachable dead external server is protected; no native process launch"
            }, indent=2) + "\n")
            assert passed, (result.stdout, result.stderr, errors, len(requests), catalog_reads)
            print(result.stdout.strip())
        finally:
            server.shutdown()
            server.server_close()
            dead_server.shutdown()
            dead_server.server_close()


if __name__ == "__main__":
    main()
