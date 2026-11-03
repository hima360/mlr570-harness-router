"""Serve Liquid AI's d1-2B decision model to the router over stdin / stdout (one JSON object per line).

d1-2B needs transformers >= 5.19 and its own model code, which would clash with the harness's environment,
so it runs in a separate venv (.venv-d1) as a long-lived child process:

  .venv-d1/bin/python -m phase2.d1_server --model LiquidAI/d1-2B --revision <sha>

Request:  {"text": "...", "questions": {name: question}}   (d1's question format, as on its model card)
Reply:    {"answers": {...}}  or  {"error": "..."}
The first line written is {"ready": true, "device": ...} once the model has loaded.
"""
import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="LiquidAI/d1-2B")
    ap.add_argument("--revision", default=None)
    a = ap.parse_args(argv)

    import torch
    from transformers import AutoModel

    dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    dtype = torch.float32 if dev == "cpu" else torch.bfloat16
    # trust_remote_code: d1 ships its own model code; the revision is pinned in config so it cannot change
    model = AutoModel.from_pretrained(a.model, revision=a.revision, trust_remote_code=True, dtype=dtype).to(dev)
    print(json.dumps({"ready": True, "device": dev}), flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            out = model.system_one(req["text"], req["questions"])
            reply = {"answers": out.get("answers", {})}
        except Exception as e:  # one bad request must not kill the server
            reply = {"error": f"{type(e).__name__}: {e}"[:500]}
        print(json.dumps(reply, default=float), flush=True)


if __name__ == "__main__":
    main()
