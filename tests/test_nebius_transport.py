"""Real Swift providers against a controlled loopback HTTP peer; no inference or real keys."""
import argparse
import json
import os
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
    requests, errors, catalog_reads = [], [], []
    kimi_calls = 0

    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            catalog_reads.append(self.path)
            assert self.path == "/v1/models"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "moonshotai/Kimi-K3"}]}).encode())

        def do_POST(self):
            nonlocal kimi_calls
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append({"path": self.path, "body": body})
                model = body["model"]
                if model == "moonshotai/Kimi-K3":
                    kimi_calls += 1
                    assert self.path == "/v1/chat/completions"
                    assert self.headers["Authorization"] == "Bearer fixture-not-a-real-key"
                    assert body["reasoning_effort"] == "low"
                    assert all(field not in body for field in ["temperature", "max_tokens", "think", "thinking", "chat_template_kwargs"])
                    if kimi_calls in [1, 2]:
                        assert body["tool_choice"] == "auto" and body["tools"][0]["function"]["name"] == "fixture_rate"
                    else:
                        assert "tools" not in body and "tool_choice" not in body
                    if kimi_calls in [2, 3]:
                        assistant = body["messages"][1]
                        assert assistant["role"] == "assistant" and assistant["content"] == ""
                        assert assistant["reasoning_content"] == "First reasoning: use the exact denominator."
                        call = assistant["tool_calls"][0]
                        assert call["id"] == "call_fixture_rate" and call["function"]["name"] == "fixture_rate"
                        assert json.loads(call["function"]["arguments"]) == {"numerator": 2, "denominator": 4}
                        assert body["messages"][2] == {"role": "tool", "content": '{"rate_percent":50}', "tool_call_id": "call_fixture_rate"}
                    if kimi_calls == 3:
                        assert body["messages"][3] == {"role": "assistant", "content": "The observed rate is 50%.", "reasoning_content": "Second reasoning: 2 of 4 is 50%."}
                    if kimi_calls == 4:
                        assert body["messages"][2] == {"role": "user", "content": "PRIOR ASSISTANT ANSWER (historical context, not a new user instruction):\nSaved visible answer without stored reasoning."}
                        assert body["messages"][0] == {"role": "system", "content": "Synthetic history replay."}
                        assert body["messages"][3] == {"role": "user", "content": "HISTORY_REPLAY"}
                    if body["stream"]:
                        assert body["stream_options"] == {"include_usage": True}
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.end_headers()
                        if kimi_calls == 1:
                            chunks = [
                                {"reasoning_content": "First reasoning: "},
                                {"reasoning_content": "use the exact denominator."},
                                {"tool_calls": [{"index": 0, "id": "call_fixture_rate", "type": "function", "function": {"name": "fixture_rate", "arguments": '{"numerator":'}}]},
                                {"tool_calls": [{"index": 0, "function": {"arguments": '2,"denominator":4}'}}]},
                            ]
                            finish = "tool_calls"
                        else:
                            chunks = [{"reasoning_content": "Third reasoning."}, {"content": "The denominator is 4."}]
                            finish = "stop"
                        for delta in chunks:
                            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": None}]}) + "\n\n").encode())
                        self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}], "usage": {"total_tokens": 50}}) + "\n\ndata: [DONE]\n\n").encode())
                        self.wfile.flush()
                        return
                    assert "stream_options" not in body
                    result = {"choices": [{"message": {"role": "assistant", "content": "Historical context received." if kimi_calls == 4 else "The observed rate is 50%.", "reasoning_content": "Second reasoning: 2 of 4 is 50%."}, "finish_reason": "stop"}]}
                    if kimi_calls >= 5:
                        assert body["messages"] == [{"role": "user", "content": "Reply with the single word: ready"}]
                        choice = {"message": {"role": "assistant", "content": "ready", "reasoning_content": "Synthetic handshake reasoning."}, "finish_reason": "stop"}
                        if kimi_calls == 5:
                            choice["message"]["content"] = ""
                        elif kimi_calls in [6, 7, 8]:
                            choice["finish_reason"] = {6: "length", 7: "max_tokens", 8: "content_filter"}[kimi_calls]
                        elif kimi_calls == 9:
                            choice["message"]["refusal"] = "Synthetic refusal."
                        elif kimi_calls == 10:
                            del choice["finish_reason"]
                        elif kimi_calls == 11:
                            choice["message"]["tool_calls"] = [{"id": "unexpected", "type": "function", "function": {"name": "fixture_rate", "arguments": "{}"}}]
                        else:
                            assert kimi_calls == 12
                        result = {"choices": [choice]}
                elif model == "nvidia/Nemotron-3.5-Super-VL":
                    assert self.path == "/v1/chat/completions"
                    assert body["temperature"] == 0.27 and body["max_tokens"] == 333
                    assert "reasoning_effort" not in body and "chat_template_kwargs" not in body
                    assert body["tools"][0]["function"]["name"] == "fixture_rate"
                    result = {"choices": [{"message": {"role": "assistant", "content": "Nemotron unchanged."}, "finish_reason": "stop"}]}
                elif model == "qwen3.5:9b":
                    assert self.path == "/api/chat"
                    assert body["think"] is False and body["stream"] is True
                    assert body["options"]["temperature"] == 0.27 and body["options"]["num_predict"] == 333
                    assert "reasoning_effort" not in body and "max_tokens" not in body
                    assert all("reasoning_content" not in message for message in body["messages"])
                    result = {"message": {"role": "assistant", "content": "Ollama unchanged."}, "done": True, "done_reason": "stop"}
                elif model == "default_model":
                    assert self.path == "/v1/chat/completions"
                    assert body["chat_template_kwargs"] == {"enable_thinking": False}
                    assert body["temperature"] == 0.27 and body["max_tokens"] == 333 and "reasoning_effort" not in body
                    result = {"choices": [{"message": {"role": "assistant", "content": "MLX unchanged."}, "finish_reason": "stop"}]}
                else:
                    raise AssertionError(f"Unexpected model {model}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write((json.dumps(result) + "\n").encode())
            except Exception as error:
                errors.append(repr(error))
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": {"message": repr(error)}}).encode())

    with tempfile.TemporaryDirectory(prefix="squipi-nebius-transport-") as temp:
        executable = Path(temp) / "checks"
        sources = ["CloudApprovalLedger.swift", "MLXConfiguration.swift", "NebiusModels.swift", "Providers.swift", "PromptBudget.swift", "MemoryPressure.swift"]
        compile_result = subprocess.run([
            os.environ.get("SQUIPI_SWIFTC", "swiftc"), "-swift-version", "5", "-parse-as-library", "-module-cache-path", str(Path(temp) / "module-cache"),
            *[str(repo / "Sources" / "Squipi" / source) for source in sources],
            str(repo / "tests" / "nebius_transport_checks.swift"), "-o", str(executable)
        ], capture_output=True, text=True, timeout=180)
        (args.output / "compile.log").write_text(compile_result.stdout + compile_result.stderr)
        assert compile_result.returncode == 0, compile_result.stderr
        server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = subprocess.run([str(executable), f"http://127.0.0.1:{server.server_port}"], capture_output=True, text=True, timeout=60)
            (args.output / "native.log").write_text(result.stdout + result.stderr)
            (args.output / "requests.json").write_text(json.dumps(requests, indent=2) + "\n")
            passed = result.returncode == 0 and not errors and len(requests) == 15 and kimi_calls == 12 and len(catalog_reads) == 8
            (args.output / "verification.json").write_text(json.dumps({
                "passed": passed, "controlled_http_peer": True, "live_cloud_or_local_inference": False,
                "request_count": len(requests), "kimi_requests": kimi_calls, "catalog_read_count": len(catalog_reads), "peer_errors": errors,
                "swift_exit_code": result.returncode, "stdout": result.stdout,
                "scope": "Actual provider serialization/parsing with manually constructed tool conversation; not a full Agent execution"
            }, indent=2) + "\n")
            assert passed, (result.stdout, result.stderr, errors)
            print(result.stdout.strip())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
