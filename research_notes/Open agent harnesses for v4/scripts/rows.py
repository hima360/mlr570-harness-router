import sys, json
out = []
for l in sys.stdin:
    r = json.loads(l)
    t = sum((r.get(k) or 0) for k in ("input_tokens", "cached_tokens", "output_tokens"))
    out.append(f"{r['harness']}/{r['task'][:12]} pass={r['passed']} tok={t} {r['duration_s']}s ${r['cost_usd']}")
print("; ".join(out))
