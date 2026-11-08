# Phase 1 setup notes (from Ibrahim's Sonnet 5.5 column)

Everything needed to run your column the same way ours ran. Code lives on `phase1/codex-harness`
(PR #6) until it is merged into `main`.

## 1. Machine

| Setting | Ours |
| --- | --- |
| Mac | Apple Silicon (arm64) |
| Colima | `colima start --cpu 14 --memory 36 --disk 100` (Colima 0.10.3, Docker 29.9.2) |
| `parallel` in config | 5 trials at once |
| `task_timeout_minutes` | 180 |

Use the same CPU, memory and `parallel` if your Mac allows (rule 10). With less, either lower `parallel`
or note the difference in the group. Disk fills fast (each task image is large): `runs/disk_guard.sh`
prunes images older than 15 minutes when the Colima disk passes 75%. Check with
`colima ssh -- df -h /var/lib/docker`.

## 2. opencodex and the bridge

Codex, Hermes and ZCode reach the model through opencodex (`ocx start`, port 10100). Containers cannot
call it directly, so `runs/ocx_bridge.py` listens on 127.0.0.1:10101 and forwards. It needs
`OCX_BRIDGE_TOKEN` in your local `.env` (any long random string you make; never commit it). Start it with:

```bash
set -a; . ./.env; set +a; nohup python3 runs/ocx_bridge.py >> runs/ocx_bridge.log 2>&1 &
```

(needs `aiohttp`). Paths under `/effort-medium/` get their reasoning effort rewritten to medium; ZCode
uses that path because its headless mode always asks for max.

## 3. Model ids for GLM-5.3 Flash

opencodex ids are `provider/model`. For GLM it is `zai/glm-5.3-flash` (check with `ocx models list`).
In your `configs/<you>.json` set `"model": "glm-5.3-flash"` and change the provider part of each harness
template:

| Harness | Ours (Sonnet) | Yours (GLM) |
| --- | --- | --- |
| Codex | `-m anthropic/{model}` | `-m zai/{model}` |
| Hermes | `-m openai/anthropic/{model}` | `-m openai/zai/{model}` |
| ZCode | `-m anthropic/{model}` | `-m zai/{model}` |

Hermes's extra `openai/` prefix tells Hermes to use an OpenAI-compatible endpoint (the bridge); the rest
is the opencodex id. Test the route before a batch (one tiny request):

```bash
set -a; . ./.env; set +a
curl -s http://127.0.0.1:10101/v1/chat/completions -H "Authorization: Bearer $OCX_BRIDGE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"zai/glm-5.3-flash","messages":[{"role":"user","content":"Reply OK"}],"max_tokens":20}'
```

After the first trials, confirm in `ocx observe logs --limit 50 --json` that `resolvedModel` is GLM and
`requestedEffort` is medium. Note: `configs/example.json` still has GLM's cached-token price at 0.0
(see `versions.md`); agree the right value in the group before your batch.

## 4. ZCode files

`runs/` is git-ignored, so build the bundle once with:

```bash
scripts/get_zcode_bundle.sh
```

It copies `zcode.cjs` and `zcode-builtin.json` from the ZCode desktop app (install it first; the app
still ships CLI 0.16.9) and downloads the Node 24.11.0 tarballs from nodejs.org, then checks every file
against the SHA-256 we ran with. If a check fails, your app has a newer CLI: tell the group before running.

## 5. How rows in `results/ibrahim.jsonl` were handled

- **Two `passed: null` Claude Code rows** are `jax-speedrun-gpu` and `live-database-cutover`. They ran
  before PR #5 excluded those two tasks (hardware) and failed at Docker start. They were kept, not
  deleted; `status.py` and the analysis skip every task in `tasks/excluded.tsv`. Your runner skips them.
- **Infrastructure re-runs.** When a trial ended because of the setup rather than the agent (disk full,
  Wi-Fi dropped, laptop restart, opencodex stall, subscription 5-hour cap), its row and folder were moved
  to `runs/void-*/` with a backup and the trial was rerun. 52 rows were set aside this way: 42 with
  `passed: null` (no result at all) and 10 recorded as failures (8 Hermes, 2 ZCode). This was not agreed
  in the group beforehand and goes beyond rule 7 as written; it is up for agreement now (see 6).
- **Hermes `photonic-waveguide-routing` r1** is one of those: the try in the log is the 4th, after three
  opencodex stalls. It still failed. Earlier rows are in `runs/void-hermes-stall*/` and in git history
  (commit 8381972).
- **Not consistent yet:** two Claude Code trials (`pretrain-shard-corruption`, `photonic-waveguide-routing`)
  were cut by the 5-hour cap and kept as failures, while Hermes cap-cut trials were rerun.

## 6. Open for the group

Proposed rule to add to `RULES.md`, so every column handles this the same way: a trial that ends because
of infrastructure (stated reason: disk, network, restart, proxy stall, plan cap) is moved to
`runs/void-<reason>/` with its row and folder kept, and rerun; the reason is posted in the group. If
agreed, Ibrahim reruns the two cap-cut Claude Code trials so his column is consistent. If not agreed,
the 10 set-aside failure rows go back into the log and the reruns are dropped.
