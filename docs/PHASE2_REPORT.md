# Phase 2: a routed harness (report section draft, 2026-11-19)

Draft for the group report. All numbers come from our run logs; cost is tokens x public list price (config files),
never the subscription bill. Sources: `reports/heldout-final.html` (held-out, 2026-11-03), `docs/OVERNIGHT_LOOP.md`,
`docs/V4_STUDY.md`, `docs/V4_DEV_RESULTS.md`.

## Question

Can a harness that sends most of the work to a cheap model, and the strong model only where it is needed, keep or
raise the strong model's pass rate at lower cost?

## Final harness: v2.8

GLM-5.3 Flash (cheap) builds the solution in the task container. Opus 5.5 then judges the build once: "ship" ends
the run, "fix" hands the container to Opus, which takes over and finishes the task. All model calls went through
the Factory Droid CLI (`droid exec`) on a Droid subscription. Earlier designs in the plan (Opus planner, per-step
routing with Laya and Arch-Router) were built and replaced by this simpler loop during development.

## Held-out result (18 tasks x 2 runs, seeds 570-572, 2026-11-03)

| setup | solved | share of hidden tests passed | total cost | cost per solve |
|---|---|---|---|---|
| Opus 5.5 alone | 4 / 35 | 68% | $31.31 | $7.83 |
| GLM-5.3 Flash alone | 2 / 34 | 63% | $3.21 | $1.60 |
| **v2.8 routed harness** | **11 / 36** | **75%** | $41.83 | **$3.80** |
| v3.5 (Opus prompt cache kept) | 7 / 36 | 76% | $35.88 | $5.13 |

v2.8 solved almost three times as many tasks as Opus alone at half the cost per solve, but its total bill was 34%
higher. Almost all of v2.8's cost (94%) is the Opus takeover, and every held-out solve came through "fix" plus an
Opus takeover. The cheap builder's value is that Opus starts from a half-built solution, not that it finishes tasks
by itself. (One held-out run of Opus alone and two of GLM alone produced no result and are left out.)

Tried and rejected after v2.8 (each on dev tasks, details in `docs/OVERNIGHT_LOOP.md`): predicting which takeovers
succeed (no separation), keeping Opus's prompt cache (v3.5: cheaper per call, fewer solves), lower reasoning
effort, Sonnet as the takeover model, capping takeover commands, GLM fixing from Opus's problem list, and skipping
takeovers when the builder hit its command limit (v3.10: cheapest per solve, but only 26 of 36 trials finished
before Droid ended, so not comparable). v4 (builder check scripts, a stricter judge, a tool probe) and v4.1 did
not beat v2.8 on the 12 dev tasks (`docs/V4_STUDY.md`).

## The backend is part of the harness: what changed when Droid ended

The Droid subscription ended on 2026-11-03 at about 08:40, during the last held-out batch; 10 of v3.10's 36 trials
never ran. From then on the only route to the same two models was opencodex, a local proxy to the Claude
subscription (Opus) and Z.ai's GLM Coding Plan. The harness policy did not change, only the backend under it.

We re-ran v2.8's policy on opencodex on the same 12 dev tasks (1 run each, 2026-11-19):

| v2.8 policy | solved | cost | cost per solve | GLM builder output tokens (median) | builder minutes | Opus takeovers that solved |
|---|---|---|---|---|---|---|
| on Droid (dv8) | 6 / 12 | $11.09 | $1.85 | 45k | 7 | 5 of 11 |
| on opencodex (v41d2) | 3 / 12 | $13.31 | $4.44 | 78k | 24 | 3 of 12 |

No task was solved on opencodex that Droid missed. Measured differences in the backend:

- **Speed:** GLM streamed about 59 output tokens/s through the Z.ai API vs about 129-141 under Droid, so the
  builder needed 2.2x the wall time for the same work.
- **Defaults:** the Z.ai API defaults GLM to maximum reasoning effort; Droid used high. Unpinned, GLM spent its
  whole budget thinking (first opencodex batch, voided).
- **Failure handling:** Droid retried rate limits and broken streams for us. On opencodex we had to add retries,
  a fix for the Opus judge writing invented tool calls for up to 64k tokens, and GLM reasoning replay.
- **Opus too:** takeovers read 40% more input and solved 25% of their tasks instead of 45%.

With 12 tasks and three tasks known to flip between runs, the drop is not proven, but v4's 21-run batch on
opencodex showed the same pattern (`docs/V4_STUDY.md`). It answers our Phase 1 question from a different side:
the same models under the same policy gave different pass rates and costs once the layer around them changed.

### Held-out on opencodex (18 tasks x 1 run, 2026-11-19/20)

To see whether v2.8's gain survives the backend change, we ran v2.8 and Opus alone once more on all 18 held-out
tasks, both on opencodex (job `hox1`, per-task table in `docs/HOX1_RESULTS.md`):

| setup | backend | solved | total cost | cost per solve |
|---|---|---|---|---|
| Opus 5.5 alone | Droid (2 runs/task) | 4 / 35 (11%) | $31.31 | $7.83 |
| v2.8 | Droid (2 runs/task) | 11 / 36 (31%) | $41.83 | $3.80 |
| Opus 5.5 alone | opencodex | 2 / 18 (11%) | $18.70 | $9.35 |
| v2.8 | opencodex | 4 / 18 (22%) | $23.22 | $5.80 |

Opus alone solved the same share on both backends; v2.8 dropped from 31% to 22% but still solved twice as many
tasks as Opus alone, at 24% more total cost and 38% less per solve. Every task Opus alone solved, v2.8 solved too
(hof-topology, sglang-qwen-burst); v2.8 added roy-polymorph-cn and sound-change-cascade. With 1 run per task this
is two tasks of difference, so it supports the direction of the Droid result, not its size. Two Opus-alone runs
ended on a provider rate limit and were rerun; none was left out.

What this means for our numbers:

1. v2.8's held-out result stands as measured, on Droid. On opencodex the routed harness still beat Opus alone on
   held-out tasks, by less: the gain depends partly on the backend, not only on the routing policy.
2. **List price vs subscription.** On a flat subscription the extra 34% list-price cost of v2.8 is spent quota, not
   money, and GLM work on a Coding Plan is close to free. The "cost" a user sees depends on how the models are
   bought, which list-price accounting hides. We report list price so the setups are comparable.
3. **Reproducibility.** A result that depends on a commercial backend can stop being reproducible when the
   subscription ends. We kept the configs, traces and code (`phase2/`, tag `phase2-v2.8`), but not the backend.

## Limits

- 18 held-out tasks x 2 runs; several tasks flip between runs, so a one- or two-solve difference is noise.
- Dev decisions were made on 12 tasks with 1 run each.
- Cost is list-price equivalent, not what we paid.
