# Phase 2 v4 plan (draft for group review, 2026-11-18)

v4 is the version we present. It keeps what works in v2.8 (GLM builds first, Opus rescues), fixes the
parts our own data shows are broken, and ships as a product: a chat app you can talk to, that shows who is
working, what it costs and how it scored. Nothing here is tuned to a task name, a test share or a benchmark.

Evidence behind every line: `reports/Open agent harnesses for v4.md`, `reports/Cheaper strong model agent
harnesses.md`, and the dev-only analyses in `research_notes/Open agent harnesses for v4/`
(`our_dev_traces.md`, `plan_revise_study.md`, with their scripts). Held-out runs were not used to design v4.

## 1. Where we stand

| Held-out, seeds 570-572 (Historical, `reports/heldout-final.html`) | Solved | Total cost | Per solve |
|---|---|---|---|
| Opus 5.5 alone | 4/35 | $31.31 | $7.83 |
| GLM-5.3 Flash alone | 2/34 | $3.21 | $1.60 |
| v2.8 (GLM builds, Opus judges, Opus takes over) | 11/36 | $41.83 | $3.80 |
| v3.5 (v2.8 + cache kept) | 7/36 | $35.88 | $5.13 |

Goal of the course question, still unmet: beat Opus alone on solves **and** total cost.

## 2. What the data says (dev runs only, Verified locally 2026-11-18)

1. **The judge is the weak link.** "Ship" is right 9 of 23 times on dev (0 of 8 on held-out). Wrong ships are
   near misses (70-93% of tests). The judge never runs the task's checks; it spends a median of 4 Opus calls
   exploring. It costs 23% of takeover-version spend.
2. **Takeovers are where solves come from, and they start cold.** 11 of 28 takeovers rescued a task GLM failed
   ($2.27 per solve). Each takeover re-explores like a fresh start (median 3 commands before its first edit):
   about $8.87 of $28.20 takeover spend. The v3.1 handoff note did not shorten it.
3. **Multi-step planning lost in v1 and was then switched off.** The first versions (Claude models, Laya routing,
   planner splitting into up to 5 steps) solved fewer than one model working alone: planner/levers/routed/split
   3 of 37 runs vs Sonnet alone 5 of 22 and Opus alone 5 of 10 (prefixes cmp, cmpd, ev, ev2, Verified locally
   2026-11-18). Since then every setup caps the plan at 1 step, so planning has never been tried with the GLM
   builder + Opus takeover design.
4. **The cheap fix round before Opus was never really tested.** Today GLM builds and, on "fix", Opus takes over.
   v2.9 added one GLM fix attempt before the Opus takeover, but its second review crashed ("Prompt file is
   empty") both times it fired and fell back to Opus. The "dropped" verdict on 2026-11-02 was invalid.
5. **Not worth building:** more cache work (waste beyond cold starts is $4.10 in all dev runs), a dependency
   cache (all installs took 249 s), loop detection and takeover caps (long takeovers are the solves).
6. **Plateau tasks:** five dev tasks no setup ever solves, Opus alone included, took 40% of dev spend. We report
   them; we do not tune for them.

Outside evidence agrees on item 1: the harness changes that moved scores on a fixed model make the agent prove
completion by running checks (LangChain +13.7 points on Terminal-Bench 2.0; "false success" is 76% of failures
in a 2026 study; LLM judges are unreliable). DeepSeek's open-source harness (MIT, release candidates only) is
competitive but not better than its peers on an independent board, so we borrow four ideas, not the framework.

## 3. What changes in v4

Every change sits behind a config switch (default off until it passes dev), has a unit test, and states why it
should help any agent task, not just this benchmark. The plan was red-teamed on 2026-11-18
(`research_notes/Open agent harnesses for v4/v4_redteam.md`); its fixes are folded in below.

### A0. First, a fair baseline (required before anything else)

Droid is gone, and with it Droid's sessions, caching and effort flags. Old numbers no longer compare. On the new
backend (opencodex: GLM via Ammar's Z.ai, Opus 5.5 via the active Claude account) we rerun **v2.8, Opus alone and
GLM alone on the 12 dev tasks, twice**. Every v4 change is judged against these runs, not against Droid-era ones.

### A. Decisions (who decides "done", who works, when to escalate)

What actually drives v2.8's cost: the judge hands over to Opus in 21 of 24 dev runs, and the takeovers are
~$9.5 of ~$12 per dev pass. The judge itself is cheap in v2.8 ($1.66). So v4 must **hand over less often
without losing rescues**. That is the target of A1 and A5.

| # | Change | Why it generalises | Keep rule (dev, vs new-backend v2.8, two passes) |
|---|---|---|---|
| A1 | **Verified done.** Before the verdict the harness runs the task's own checks if it has any, plus tests the builder wrote, and the judge must map each test to a spec item. The judge sees spec + test output and decides in one call. Green, spec-mapped checks let it ship; the takeover is no longer the default. Fix the bug where the judge says "ship" when it runs out of turns (`phase2/review.py:210`); it becomes "fix", logged. | "Done means the checks pass" holds for any task with a spec. | Takeovers down at least 25%, solves within 1, at most 1 extra wrong ship. Log ship rate and ship precision per run. |
| A5 | **The router (course question).** A small local model reads the task plus GLM's first few turns and decides: keep GLM, or escalate early to Opus. Early turns predict difficulty far better than the task text (published: 0.78 vs 0.63 routing score; text-only routers near chance). Trained on Phase 1 and dev logs only. | Uses behaviour signals, not task names; the same router runs on any benchmark. Generality is checked in section 4. | Offline first on logged runs (no quota): must beat "always hand over" on cost at equal solves before any paid run. |
| ~~A3~~ | **Cheap fix round: dropped.** Full GLM-5.3 costs ~9x Flash per token ($1.40/$4.40 vs $0.15/$0.50), lost as a takeover (v3.0: 1/7 vs 4/7), and in-loop revise rescued 1 of 19 runs. Not worth Ammar's quota. | | |
| A2 | **Warm handoff (test first).** The takeover gets a fixed-shape fact sheet: files changed, check output, open spec items, not GLM's claims. | Any handoff loses state. | Median commands before the takeover's first edit falls from 3 to 1 or less. Red team: v3.1's handoff went 2/7 vs 4/7, so low odds. |
| ~~A4~~ | **Multi-step planning: dropped from v4.** Lost in v1 (3/37 vs 5/22 one-model); highest cost to retest, lowest prior. Reported as a finding. | | |

### Models considered and not used

| Model | Price per M (in/out) | Evidence | Use in v4 |
|---|---|---|---|
| Haiku 5.5 | $0.10 / $0.50 | Reachable (Verified live 2026-11-18). As a single model in v1: 0/12 dev runs (cmp, cmpd). | Not a builder. Candidate only for mechanical A1 work (running checks, filling the fact sheet), if it is cheaper than the harness doing it itself. |
| Sonnet 5.5 / Sonnet 5 | $2 / $10 | Phase 1: 51/162 vs GLM Flash 43/160 at twice the cost per solve; Sonnet 5 takeover (v3.7) lost 2 solves. | None. |
| GLM-5.3 (full) | $1.40 / $4.40 | v3.0 takeover 1/7 vs v2.8 4/7. | None. |

### B. Worker hygiene (cheap, no paid run needed)

| # | Change | Status |
|---|---|---|
| B1 | Tool probe at start (python, python3, pip, file, ps; working dir), added to the existing bootstrap. | Build. 7% of failed commands were "command not found". |
| B4 | Stop after 2 "container not running" outputs; log as infrastructure. | Build. 160 wasted commands on dead containers. |
| B5 | Keep background services alive at exit; block fetches of reference solutions. | Check first: our harness may already be fine. |
| B6 | Declare each model's context window and max output; log every model request in full. | Build. Needed for the app and replay. |
| B3 | Per-message effort in the takeover. | Later, test first: it tunes Opus, not who works. |
| ~~B2~~ | Output cap. | Already exists (`max_observation_chars` 10000, head + tail). |

### C. Running it (no more lost runs)

- **Backend:** configs move from `droid/` ids to opencodex model ids (`claude-opus-5-5` answers, Verified live
  2026-11-18).
- **Preflight:** before a batch, check proxy, bridge, Claude route and one test call per model; refuse to start
  otherwise. A run that logs 0 tokens is set aside and retried automatically.
- **Quota guard:** stop launching runs at 80% of Ammar's weekly GLM quota; GLM only off-peak (14:00-10:00 Dubai).
- **Cost:** tokens x list prices in config, labelled "estimated", never the subscription bill.
- **Budget per run:** target under $0.87 per run (Opus alone on held-out: $31.31 / 36). v2.8 is $0.92 on dev.

### D. The app

A product, not a demo script. A Python server (FastAPI) streams the events the harness already logs (`Trace.log`:
plan, route, llm_call, takeover, review, done) to a web app built on assistant-ui (the chat kit Hermes Desktop
uses, MIT) with Vercel AI Elements for plan, task and token panels (Apache-2.0). Our own visual identity.

- **Chat view:** type a task in plain language, like ChatGPT or Claude. Replies stream.
- **Task panel (right side):** the live plan and steps, each with status, the model on it, tokens, estimated
  cost and a timer.
- **Who's working:** live badge for GLM builder / judge / router / Opus takeover, with each handoff and why.
- **Cost meter:** tokens in, cached, out, and estimated API cost per model and in total.
- **Connections:** which providers are connected, their last success or failure, and quota left.
- **Benchmark view:** replay a recorded benchmark run step by step with the verifier's result, labelled as a
  recording; or launch a rerun of one task through Harbor and watch it live.
- **Order:** replay and benchmark views first (they need no new runtime). The live chat view needs a workspace
  outside a Harbor container (local Docker sandbox behind the same `environment.exec`); it is built after the
  held-out code freeze so it cannot change the measured harness.

## 4. How we test it (and avoid benchmaxing)

1. **A0 baseline** on the new backend (v2.8, Opus alone, GLM alone; dev, two passes).
2. **One change at a time** on dev, two passes each, red-teamed before building. +-2 dev solves is noise
   (dv8 vs dv13 swapped tasks both ways), so keep rules use takeover counts and cost, not a single solve.
3. **Freeze the code**, then held-out. The old held-out pool is used up: `scripts/draw_heldout.py` finds
   0 eligible never-run tasks. Options, to be decided **before** drawing: (a) loosen the filters, (b) use
   Phase 1 `plan.tsv` tasks Phase 2 never saw, (c) a second benchmark. Arms: v4, v2.8, Opus alone, GLM alone,
   same backend.
4. **Generality check:** a small set from a second benchmark Harbor supports (e.g. SWE-bench Verified), v4 vs
   Opus alone, no tuning. If v4 only wins on Terminal-Bench, we say so.
5. **Reporting:** solves, total cost, cost per run vs $0.87, cost per solve, with bootstrap confidence
   intervals. With ~36 runs, v4 vs v2.8 differences will not be significant; we say so instead of over-claiming.
6. **Success bar (written now):** v4 beats Opus alone on solves and total cost on the new held-out set.

## 5. Order of work

| Step | Work | GLM quota |
|---|---|---|
| 1 | Backend port, preflight, quota guard, review.py bug, B1/B4/B6; app server + replay view | None |
| 2 | A0 baseline: 3 arms x 12 dev tasks x 2 | Est. 10-15% weekly (Unverified) |
| 3 | A1 built and tested (2 passes); A5 router trained and scored offline | Est. 5-10% |
| 4 | A2 probe if A1 lands | Est. 5% |
| 5 | Freeze; held-out draw (method decided beforehand); 4 arms; generality check | Largest; stop at 80% |
| 6 | Live chat view, polish, write-up | None |

## 6. Decisions the group owes

1. Approve the success bar and the held-out source (a, b or c in section 4) before any held-out run.
2. Approve spending Ammar's GLM quota (stop at 80% weekly; A0 alone is about 10-15%).
3. Whether the generality check (second benchmark) is in scope.
