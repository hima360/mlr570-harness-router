"""Scripted OpenAI-compatible server for the Harbor smoke test (no real model).

Planner request -> a one-step plan. Worker requests -> write the file, then submit.
  python tests/harbor_smoke/stub_llm_server.py 18999
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PLAN = {"steps": [{"id": "s1", "goal": "Create /app/hello.txt containing the word hello",
                   "depends_on": [], "writes": ["/app/hello.txt"], "check": "grep -q hello /app/hello.txt"}]}
SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def reply(body):
    msgs = body.get("messages", [])
    text = json.dumps(msgs)
    msg = {"role": "assistant", "content": ""}
    if "You are planning work" in text:
        msg["content"] = json.dumps(PLAN)
    else:
        n_tool = sum(1 for m in msgs if m.get("role") == "tool")
        cmd = "echo hello > /app/hello.txt" if n_tool == 0 else f"printf '{SUBMIT}\\nwrote /app/hello.txt\\n'"
        msg["tool_calls"] = [{"id": f"call_{n_tool}", "type": "function",
                              "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}]
    return {"id": "stub", "object": "chat.completion", "created": int(time.time()), "model": body.get("model"),
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if "tool_calls" in msg else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data = json.dumps(reply(body)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 18999), H).serve_forever()
