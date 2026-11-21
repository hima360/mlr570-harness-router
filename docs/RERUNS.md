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
| vllm-deepseek-streaming | claude-code | 1 | null, 0 tokens | failed (4 of 5 tests), 7560 s | 24.66M | $0.9895 |

The claude-code row was rerun on 2026-11-18 (off-peak, 15:24 to 17:30 Dubai, runner commit `70ab2aa`).
The agent finished on its own (no timeout, no exception) and its answer failed the verifier, so the row
stays as a failure. Ammar's column is back to 160 rows.

Z.ai quota used: first three runs, 5-hour window 1% to 14% and weekly 30% to 33%; claude-code run,
5-hour 1% to 7% and weekly 33% to 35%.

Attempts that failed before reaching the model this session (0 tokens, also moved aside, not counted):
the bridge on port 10101 was not running (3 rows), and the restarted proxy had Claude inbound off
(1 row; `claudeCode.enabled` in `~/.opencodex/config.json`, read only at proxy start).

## Mahek, GPT-6.1 Sol, reruns 2026-11-19 to 2026-11-20 on Mahek's machine

Reason: setup failures and runs cut off by the machine, not agent results. 78 rows were set aside under
rule 7 (counting repeat failures of the same run). Old rows and
their run folders are kept under `runs/void-<reason>-mahek-1119/` (`voided_rows.jsonl` in each, committed
here; the run folders are not in git). Reruns used `configs/mahek_rerun.json` and `configs/mahek_rerun2.json`
(same settings as `configs/mahek.json`, only the plan file differs), runner commits `70ab2aa`, `9f30c10`
and `7fe0989`.

| Void folder | Rows | What happened |
|---|---|---|
| void-swe-windows-filenames | 19 | swe repos failed to check out on Windows (`:Zone.Identifier` file names); fixed with `core.protectNTFS=false` for the run_grid window only |
| void-agent-setup-timeout | 20 | agent install did not finish in the 18-minute setup limit, 0 tokens |
| void-instant-fail | 13 | died within about 60 seconds, 0 tokens or empty folder, no error file; cause not found |
| void-env-start-timeout | 8 | environment start passed the 30-minute limit (webclients image, 4 harnesses, twice) |
| void-hermes-install | 4 | Hermes agent install failed |
| void-docker-restart | 3 | Docker restarted during the run (two of these were logged as passes with 0 tokens) |
| void-network-install | 3 | takens, apt install failed on the network |
| void-memory-stopped | 2 | vpp-loss-divergence, stopped with `docker stop` because two runs together passed the memory limit |
| void-setup-no-error | 2 | sglang and element-web with Hermes, 0 tokens, no error shown |
| void-stale-process | 2 | started by an old run_grid process; died within 25 s |
| void-verifier-timeout | 2 | verifier did not finish in 30 minutes |

Setup was slow on this machine for the whole period. A clean container needed 13 min 51 s for
`apt-get install curl nodejs npm`. The claude-code, codex and hermes commands in `configs/mahek.json` carry
`--agent-setup-timeout-multiplier 3` (18-minute limit instead of the default 6); zcode has none. The cause
(connection, WSL2 disk speed or antivirus scanning) was not confirmed.

Final state of the column: 136 rows, 57 passed, 11 left as `passed: null`, not rerun (left in the log as nulls
with the reasons below, not set aside again).

| Task | Harness | Time | Reason |
|---|---|---|---|
| vpp-loss-divergence | codex | 1118 s | agent setup timeout, 0 tokens |
| wal-recovery-ordering | claude-code | 1118 s | agent setup timeout, 0 tokens |
| glycan-ms2-elucidation | claude-code | 1150 s | agent setup timeout, 0 tokens |
| vllm-deepseek-streaming | claude-code | 1143 s | agent setup timeout, 0 tokens |
| hof-topology-interpenetration | claude-code | 1209 s | agent setup timeout, 0 tokens |
| pretrain-shard-corruption | claude-code | 25 s | died within 25 s, no error file |
| takens-embedding-lean | codex | 20 s | died within 25 s, no error file |
| takens-embedding-lean | zcode | 19 s | died within 25 s, no error file |
| takens-embedding-lean | claude-code | 3417 s | verifier timeout (30 min); agent ran, 100k tokens |
| takens-embedding-lean | hermes | 4815 s | verifier timeout (30 min); agent ran, 299k tokens |
| element-web | hermes | 1405 s | 0 tokens, no error shown (setup, not confirmed) |

The two takens verifier timeouts are not setup failures: the agents ran and the checker did not finish
(takens peaks at about 63% of Docker's 3.7 GB limit).

Sol cached price: `gpt-6.1-sol` has `cached_tokens: 0.0` in the price tables; costs in this file are
understated until that is corrected (see the price PR).

## Before a rerun, check

- `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:10101/` returns 401 (bridge up).
- For claude-code: the proxy was started with `claudeCode.enabled: true`, else every call gets 403.
- One test call through the bridge returns a completion.
