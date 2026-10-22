# Rules

These keep the three model columns comparable. If a rule blocks you, raise it in the group
before working around it.

1. **Same plan for everyone.** `plan.tsv` is generated once with seed 570 and never edited.
   Changing the task count after the pilot means regenerating it for all three, agreed in the group.
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
7. **Failures are data.** Keep failed and timed-out runs in the log. The only rows you may delete
   are `passed: null` rows caused by a parsing bug, and only after fixing `parse_result()`, so they rerun.
8. **Code changes go through PRs.** Fixes to `run_grid.py` or `make_plan.py` go on a branch with
   a short description; anyone can review. Pull before you start a batch.
9. **Push every night.** Commit and push your log daily, then post your run count in the group.
10. **Same machine habits.** Plugged in, on a stable network, nothing else heavy running during
   batches, so wall-time numbers are comparable.
