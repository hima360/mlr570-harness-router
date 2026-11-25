# Phase 1 setup notes (from Ibrahim's Sonnet 5.5 column)

How the Sonnet 5.5 column was run: machine, opencodex bridge, model ids, ZCode files and how rows were
handled. The Codex harness code (PR #6) is merged into `main`.

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
`requestedEffort` is medium. `configs/example.json` now has GLM's cached-token price at $0.03/M
(Sol: $0.10/M); see `versions.md`.

## 4. ZCode files

`runs/` is git-ignored, so build the bundle once with:

```bash
scripts/get_zcode_bundle.sh
```

It copies `zcode.cjs` and `zcode-builtin.json` (ZCode CLI 0.16.9) from `vendor/zcode-0.16.9/` in this repo,
downloads the Node 24.11.0 tarballs from nodejs.org, and checks every file against the SHA-256 we ran
with. No ZCode app is needed. Newer ZCode apps (3.17.2, for example) ship a different CLI, so do not
copy the files from your app.

## 5. How rows in `results/ibrahim.jsonl` were handled

- **Two `passed: null` Claude Code rows** are `jax-speedrun-gpu` and `live-database-cutover`. They ran
  before PR #5 excluded those two tasks (hardware) and failed at Docker start. They were kept, not
  deleted; `status.py` and the analysis skip every task in `tasks/excluded.tsv`. The runner skips them.
- **Infrastructure re-runs.** When a trial ended because of the setup rather than the agent (disk full,
  Wi-Fi dropped, laptop restart, opencodex stall, subscription 5-hour cap), its row and folder were moved
  to `runs/void-*/` with a backup and the trial was rerun. 54 rows were set aside this way: 42 with
  `passed: null` (no result at all) and 12 recorded as failures (8 Hermes, 2 ZCode, 2 Claude Code).
  This was done before the rule existed; it is now covered by `RULES.md` rule 7 (see 6).
- **Hermes `photonic-waveguide-routing` r1** is one of those: the try in the log is the 4th, after three
  opencodex stalls. It still failed. Earlier rows are in `runs/void-hermes-stall*/` and in git history
  (commit 8381972).
- **Cap-cut Claude Code trials, rerun.** Two Claude Code trials (`pretrain-shard-corruption`,
  `photonic-waveguide-routing`) were cut by the 5-hour cap and first kept as failures. They were rerun on
  2026-11-08 (commit `25fec04`, Claude Code 2.1.316) and both passed. The old rows and folders are kept in
  `runs/void-cc-5hcap-1809`, so the column is consistent with the Hermes cap-cut reruns.

## 6. Outcome: rerun rule agreed

`RULES.md` rule 7 now covers this: a trial that ends because of the setup (disk, network, restart,
opencodex stall or error, subscription cap) is moved to `runs/void-<reason>/` with its row and folder
kept, and rerun; the task and reason are posted in the group. It was proposed on 2026-11-08 (PR #7,
commit `27e79ea`) and agreed and merged on 2026-11-17 (PR #7, merge `99d1d46`), the same day PR #11
(merge `93e6503`) extended it to runs that hit a time limit, rerun with the same limits. Wrong answers
and agent crashes stay in the log. The set-aside rows described in section 5 therefore stay set aside.
