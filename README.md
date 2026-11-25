# MLR570: harness-aware, cost-efficient model routing

Course project for MLR570 (Advanced Machine Learning). It asks whether the agent harness changes cost
and pass rate, and whether a harness that defers to a strong model only when needed can cut cost per
solved task. The write-up is in `paper/`.

1. **Phase 1, harness study.** 3 models (Claude Sonnet 5.5, GPT-6.1 Sol, GLM-5.3 Flash) x 4 coding-agent
   harnesses (Claude Code, Codex CLI, Hermes Agent, ZCode), 40 runs per configuration, 480 runs in total.
   Per run we record pass/fail, tokens, cost and time.
2. **Phase 2, cascade harness.** Our own harness, run as a Harbor external agent. The final version is
   **v2.8** (tag `phase2-v2.8`). It keeps each task whole in one container: GLM-5.3 Flash builds first,
   Claude Opus 5.5 reviews the result once and takes over only if needed, so the decision to defer to the
   strong model is made after the inexpensive model's attempt, not from the prompt. A first design that
   split each task into steps and routed each step (planner, Laya, Arch-Router, tier table, verifier) was
   dropped: Terminal-Bench tasks proved tightly coupled, and routing from task text did not separate tasks
   by required capability (`paper/sections/05_phase2_method.tex`).

## Phase 1 results

| Model | Solved | Cost (list prices) |
| --- | --- | --- |
| GPT-6.1 Sol | 65 / 160 (41%) | $249.99 |
| Claude Sonnet 5.5 | 51 / 160 (32%) | $135.76 |
| GLM-5.3 Flash | 43 / 160 (27%) | $58.02 |

- The harness matters. GLM-5.3 Flash solves between 4 and 16 of 40 depending on the harness.
- In Claude Code, GLM matches Sonnet (16 / 40 each) at $0.63 per solve against $2.13.
- GPT-6.1 Sol costs $17.83 in Codex CLI and $126.05 in ZCode for 18 and 17 solves out of 40.
- All costs are tokens x the public list price in the config, not what any subscription charged.

Per-configuration numbers: `paper/sections/04_phase1_results.tex`; plan and facts: `docs/PAPER_PLAN.md`.

## Phase 2 results (final version v2.8)

GLM-5.3 Flash builds. Opus 5.5 reviews once and, when the review says "fix", takes over in the same
container. Evaluation tasks, 18 tasks x 2 runs:

| Setup | Solved | Cost | Per solve |
| --- | --- | --- | --- |
| **v2.8 routed harness** | 11 / 36 | $41.83 | $3.80 |
| Opus 5.5 alone | 4 / 35 | $31.31 | $7.83 |
| GLM-5.3 Flash alone | 2 / 34 | $3.21 | $1.60 |

- An exploratory comparison on Z.ai (via opencodex) gave 4 / 18 for v2.8 against 2 / 18 for Opus alone.
- v3.10, a cheaper variant, stopped at 26 of 36 trials when the Droid subscription ended, so it is not
  compared on the full evaluation set.
- Details: `docs/PHASE2_REPORT.md`, `paper/sections/07_phase2_results.tex`, design in `docs/PHASE2_PLAN.md`.

## Repo layout

| Path | What it is |
| --- | --- |
| `results/` | Phase 1 run logs, one JSON line per run (`results/<person>.jsonl`). |
| `docs/` | Plans, the Phase 2 report, setup notes, the overnight-loop log. |
| `phase2/` | The routed harness (Harbor external agent `phase2.agent:RoutedHarness`), TUI, setups. |
| `scripts/` | Evaluation drivers, progress, HTML report, ZCode bundle. |
| `paper/` | LaTeX source of the paper (see `paper/README.md`). |
| `configs/` | Phase 1 run configs (prices, harness commands) and Phase 2 backend configs. |
| `run_grid.py`, `make_plan.py`, `plan.tsv`, `tasks/` | Phase 1 runner and the task plan (seed 570). |
| `RULES.md`, `versions.md` | Benchmark rules and pinned versions of every harness, Harbor and opencodex. |
| `tests/` | Tests with a fake environment and fake LLM (no network, no Docker). |
| `vendor/zcode-0.16.9/` | Third-party ZCode 0.16.9 CLI bundle used by the ZCode harness (see `scripts/get_zcode_bundle.sh`). |

Phase 1 log fields: `ts, person, benchmark, task, run, harness, model, passed, exit_code, duration_s,
input_tokens, output_tokens, cached_tokens, tool_calls, cost_usd, runner_commit, outdir`.

## Reproduce

Phase 2 setup and tests:

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv -r requirements-phase2.txt
.venv/bin/python -m pytest tests -q
```

Harbor smoke test with a scripted model server (no subscription needed; needs Docker with `docker compose`):

```bash
.venv/bin/python tests/harbor_smoke/stub_llm_server.py 18999 &
PYTHONPATH=$PWD .venv/bin/harbor run -p tests/harbor_smoke/hello-file -a phase2.agent:RoutedHarness \
  --ak base_url=http://127.0.0.1:18999/v1 --ak policy=fixed:0 -n 1 -o jobs --job-name smoke
.venv/bin/python -m phase2.show_trace jobs/smoke/*/agent
```

Policies (`--ak policy=...`): `cascade` (Laya, Arch-Router below the confidence threshold), `laya`, `arch`
and `random` belong to the earlier per-step routing design (`docs/PHASE2_PLAN.md`), which was dropped; the
`routed` and `split` setups in `phase2/setups.py` use `laya`. `fixed:<tier>` runs every step on one tier;
the smoke test above uses `fixed:0` as a single-model baseline. The final v2.8 is setup `takeover28` in
`phase2/setups.py`: policy `fixed:1` with `configs/phase2-droid-glm-takeover28.json`.

Command line and TUI:

| To | Do |
|---|---|
| Be asked what to do, step by step | `./harness` |
| Try it on a tiny bundled project | `./harness try` |
| Give it your own task on your own folder | `./harness run "fix the failing login test" ~/my-project --check "pytest -q"` (works on a copy in a sandbox, shows the diff, asks before applying) |
| Check the setup | `./harness doctor` |
| Watch runs, compare results, start a run | `.venv/bin/python -m phase2.tui` (keys: `n` new run, `c` compare, `/` filter, `?` help) |
| Get a results page for a browser | `.venv/bin/python scripts/report_html.py <prefix> [<prefix> ...]` (writes `reports/`) |
| See how far an evaluation is | `.venv/bin/python scripts/progress.py [prefix] [-w]` |

Phase 1 runs need Docker, Harbor and opencodex at the versions in `versions.md`; the runner is
`python3 run_grid.py configs/<name>.json` and the rules that keep columns comparable are in `RULES.md`.
Setup details (machine, opencodex bridge, ZCode files): `docs/PHASE1_SETUP_NOTES.md`. Tokens and keys
live in a local `.env` that is never committed.
