# Phase 1 reruns (RULES.md 7)

Every row set aside and rerun under rule 7. The old rows and their run folders are kept, not deleted,
under `runs/void-<reason>/` on the machine that ran them (`runs/` is not in git).

## Ammar, GLM-5.3 Flash, rerun 2026-11-17 on Ibrahim's machine

Reason: setup failure. All four original rows logged 0 tokens and `passed: null`, so the agent never
reached the model. Rerun with Ammar's Z.ai GLM Coding Plan (endpoint `api.z.ai/api/coding/paas/v4`,
off-peak), same `configs/ammar.json` (parallel 2, 180-minute limit), runner commit `93e6503`.
Old rows: `runs/void-setup-ammar-1117/voided_rows.jsonl` on Ibrahim's machine.

| Task | Harness | Run | Old row | New row | Tokens | Cost |
|---|---|---|---|---|---|---|
| vllm-deepseek-streaming | codex | 1 | null, 0 tokens | passed, 714 s | 3.11M | $0.1124 |
| vpp-loss-divergence | hermes | 1 | null, 0 tokens | passed, 3116 s | 37.50M | $1.1848 |
| interleaved-vigenere | hermes | 2 | null, 0 tokens | passed, 5162 s | 19.19M | $0.6365 |
| vllm-deepseek-streaming | claude-code | 1 | null, 0 tokens | **pending** | | |

The claude-code row is not in `results/ammar.jsonl` until its rerun lands. Until then Ammar's column
has 159 rows, not 160.

Z.ai quota used by the three runs: 5-hour window 1% to 14%, weekly 30% to 33%.

Attempts that failed before reaching the model this session (0 tokens, also moved aside, not counted):
the bridge on port 10101 was not running (3 rows), and the restarted proxy had Claude inbound off
(1 row; `claudeCode.enabled` in `~/.opencodex/config.json`, read only at proxy start).

## Before a rerun, check

- `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:10101/` returns 401 (bridge up).
- For claude-code: the proxy was started with `claudeCode.enabled: true`, else every call gets 403.
- One test call through the bridge returns a completion.
