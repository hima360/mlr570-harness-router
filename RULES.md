# Rules

These keep the three model columns comparable. If a rule blocks you, raise it in the group
before working around it.

1. **Same plan for everyone.** `plan.tsv` is generated once with seed 570 and never edited.
   Changing the task count after the pilot means regenerating it for all three, agreed in the group.
   Tasks in `tasks/excluded.tsv` (environments needing a GPU or more than 14 CPUs) are skipped by the runner and
   left out of the analysis for everyone; `plan.tsv` itself is not edited. Phase 1 = 40 runs per harness.
2. **Frozen settings.** Prices, log fields, timeouts and `parallel` change only through a PR
   that the other two approve, and never once Phase 1 runs have started.
3. **Pinned versions.** Use the versions in `versions.md`. Turn off auto-updates for every
   harness during Phase 1. Upgrading mid-run makes earlier results incomparable.
4. **Clean harnesses.** Every harness runs with default settings: no personal skills, CLAUDE.md
   or AGENTS.md, memory, plugins, MCP servers, hooks or custom prompts. Use a dedicated profile
   or account for benchmark runs.
5. **No secrets in the repo.** Tokens, API keys, `.env` files and login files never get committed.
   `.gitignore` covers the usual names; check `git status` before every push anyway.
6. **Your log only.** Write only to `results/<your-name>.jsonl`. Append, never rewrite.
   Never edit someone else's file.
   Exception (agreed 2026-11-17, PR "GLM cached price"): `cost_usd` may be rewritten when the price table
   itself was wrong, only by `scripts/recompute_cost.py`, only that field, by the file's owner, and the price
   fix and the rewrite land in the same approved PR. Token counts, `passed` and every other field stay as logged.
   Applied twice: GLM-5.3 Flash cached tokens were priced at $0 instead of the $0.03/M list price
   (https://docs.z.ai/guides/overview/pricing); GPT-6.1 Sol cached tokens were priced at $0 instead of the
   $0.10/M standard list price (https://developers.openai.com/api/docs/models/gpt-6.1-sol, agreed 2026-11-20).
   For the Sol fix the shared price table changes in its own PR first; each owner then rewrites `cost_usd` in
   their own file with `scripts/recompute_cost.py` in a follow-up PR (only Mahek's column uses Sol).
7. **Failures are data.** Keep failed and timed-out runs in the log. The only rows you may delete
   are `passed: null` rows caused by a parsing bug, and only after fixing `parse_result()`, so they rerun.
   One exception: a run that ended because of the setup, not the agent, may be set aside and rerun.
   That covers a full disk, a dropped network, a machine restart, an opencodex stall or error, and a
   subscription usage cap. Move its row to `runs/void-<reason>/voided_rows.jsonl` and its folder into
   the same directory (keep both, never delete), rerun it, and post the task and reason in the group.
   A run that hit a time limit (the runner's 180-minute limit or a Harbor agent timeout) may be set aside
   and rerun the same way, with the same limits (agreed 2026-11-17). Wrong answers and agent crashes are not
   setup failures: they stay in the log.
8. **Code changes go through PRs.** Fixes to `run_grid.py` or `make_plan.py` go on a branch with
   a short description; anyone can review. Pull before you start a batch.
9. **Push every night.** Commit and push your log daily, then post your run count in the group.
10. **Same machine habits.** Plugged in, on a stable network, nothing else heavy running during
   batches, so wall-time numbers are comparable.
