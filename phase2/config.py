"""Harness settings: model tiers, prices, routing policy and limits.

Model ids are opencodex ids (`provider/model`). The Sonnet 5.5, Opus 5.5 and Sol ids are
unconfirmed until `ocx models list` shows them; edit `configs/phase2.json`, not this file.
"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Tier 0-2 are what the router picks; tier 3 is reached only by escalation (and the planner).
DEFAULT_TIERS = [
    "zai/glm-5.3-flash",
    "gpt-6.1-sol",
    "anthropic/claude-sonnet-5-5",
    "anthropic/claude-opus-5-5",
]

# USD per million tokens, list prices (Anthropic and Z.ai pricing pages, checked 2026-11-17); same as list_prices.py.
DEFAULT_PRICES = {
    "zai/glm-5.3-flash": {"input": 0.15, "cached": 0.03, "output": 0.50},
    "gpt-6.1-sol": {"input": 2.0, "cached": 0.0, "output": 10.0},
    "anthropic/claude-sonnet-5-5": {"input": 2.0, "cached": 0.10, "output": 10.0},
    "anthropic/claude-opus-5-5": {"input": 4.0, "cached": 0.20, "output": 20.0},
}


@dataclass
class HarnessConfig:
    tiers: list[str] = field(default_factory=lambda: list(DEFAULT_TIERS))
    planner_model: str = "anthropic/claude-opus-5-5"
    # cascade | laya | arch | random | fixed:<tier>
    policy: str = "cascade"
    confidence_threshold: float = 0.35
    base_url: str = "http://127.0.0.1:10100/v1"
    api_key: str = "local"
    prices_per_million: dict = field(default_factory=lambda: dict(DEFAULT_PRICES))
    prices_source: str = ""  # where and when the list prices were checked
    # CLI models only: model id -> effort level (droid -r / claude --effort). Missing = CLI default.
    reasoning_effort: dict = field(default_factory=dict)
    # Per CLI call. The slowest healthy call seen took 41 s; a stalled provider stream hung for 10 min.
    cli_timeout_sec: int = 180
    # Per-model override, model id -> seconds. 2026-10-28: Opus 5.5 on hard steps hit "no reply in 180 s" 6 times in
    # 12 harness runs (Sonnet 0 in 10), and one run ended on two in a row.
    cli_timeout_by_model: dict = field(default_factory=dict)
    # Per worker step attempt. step_idle_sec ends it only when no model reply came for that long (a slow
    # but working model keeps going); step_wall_time_sec is a hard safety cap. Either way the verifier
    # then fails the step and it escalates, with the attempt's notes handed over.
    step_idle_sec: int = 600
    step_wall_time_sec: int = 3600
    # Long command outputs are cut to head + tail: every CLI call re-sends the whole conversation,
    # and raw dumps made deepseek turns take 5-14 min.
    max_observation_chars: int = 10000
    max_plan_steps: int = 8
    # Path to an earlier run's trace.jsonl (or its trial / agent dir): reuse that plan instead of calling
    # the planner, so routing conditions are compared on the same DAG.
    plan_from: str = ""
    # A plan with no parallel work (a pure chain) runs as one step with the planned steps as its checklist.
    merge_chains: bool = False
    # Error handler: one fresh call per failed step (no shell, picks one action from a fixed menu).
    # "" = off: a failed step escalates once, as before. Capped at max_interventions per run.
    fixer_model: str = ""
    max_interventions: int = 2
    # Escalations per step (one tier up each). 0 for fixed-model baselines, so they stay on their model.
    max_escalations: int = 1
    # Spend caps per run (0 = off), in list-price USD and/or Factory credits. Over the cap the harness starts
    # no new attempt, escalation, handler call or step. The escalation-only top tier (Opus / Sol) is entered
    # only while spend is under top_tier_budget_frac of the cap: 2026-10-26, failing tasks escalated to Opus
    # cost up to 3.3x always-Sonnet (heat-pump-warranty $3.45 vs $1.05) for no gain.
    max_run_cost_usd: float = 0.0
    max_run_credits: float = 0.0
    top_tier_budget_frac: float = 0.5
    # A cheap model from another family that checks published facts against the transcript on submit
    # ("" = off). Its sends-back share the submit gate's submit_checks.
    reviewer_model: str = ""
    # An independent agent writes acceptance tests from the task text in parallel ("" = off); final steps must
    # pass them once they are ready.
    acceptance_model: str = ""
    acceptance_step_limit: int = 15
    # Best-of-N: a final step's first attempt runs as parallel candidates, one per model listed (each in its own
    # copy of the work folder); the best is kept. [] = off. File-based tasks only.
    best_of_models: list = field(default_factory=list)
    # The planner as tech lead: read-only exploration commands before planning (0 = off), and check-ins that
    # revise steps not yet started after another step ends (0 = off).
    planner_explore: int = 0
    laya_head: str = ""  # head.json from phase2.train_head, for policy "trained"
    planner_top_tier: bool = False  # policy "planner": a step rated 2 (hard) starts on the top tier, not escalation-only
    # A plan the planner made with ONE step runs like the single-agent baseline: on this tier, with twice the step
    # budget (-1 = off). 2026-10-28: payments got 1-step plans rated hard, ran on Opus and lost to plain Sonnet.
    single_step_tier: int = -1
    # "author": the planner's goals and checks bind the workers. "advisor": it says what must be true, its ideas go to
    # workers as hypotheses, final steps get no planner check, and a plan without parallel work becomes one step.
    planner_role: str = "author"
    # Lead and helpers (phase2.delegate): name -> model id, cheapest first. {} = off. A step's worker may then run
    # `delegate <name> <<'TASK' ... TASK` and gets the helper's report back; a sub-task with no name is routed by
    # helper_policy over the helpers (index 0 = cheapest).
    helper_models: dict = field(default_factory=dict)
    # Senior review (phase2.review): when a worker submits, this stronger model reviews the task, the transcript and
    # the diff (it may run senior_review_explore read-only commands) and can send the work back once per round.
    senior_reviewer_model: str = ""
    senior_review_rounds: int = 1
    senior_review_explore: int = 4
    # After the worker's fix, a second look at only the raised problems; any still open go to senior_fix_model
    # ("" = the reviewer's model) for one attempt of at most senior_fix_step_limit turns. Off: measured 2026-10-29
    # (ev8 vs ev7, same 10 runs) it solved the same 5/10 for 1.69M more credits, and the second look judged a
    # problem fixed that the hidden tests still failed.
    senior_verify: bool = False
    # v2.5: the worker must measure the task's stated targets the way its tests would and report the numbers; the
    # senior reviewer must back every problem with evidence (phase2.worker.MEASURE_HINT, phase2.review.EVIDENCE_RULE).
    measure_targets: bool = False
    # v2.6: every command's output ends with the time and commands used; at wrap_up_frac of either, the worker is told
    # to finish and submit, so the senior review runs with time left for a fix (2026-11-01 held-out: 15 of 36
    # GLM-builder runs hit the 45-min or 80-command limit before submitting, and those runs were never reviewed).
    clock: bool = False
    wrap_up_frac: float = 0.7
    # v2.7 (phase2.strategy): this model picks a work mode and writes a brief before the worker starts ("" = off), and
    # at checkpoint_frac of the commands or time the harness submits for a worker that has not been reviewed yet, so
    # the senior review runs with budget left to act on it (0 = off; needs clock and a senior reviewer).
    strategy_model: str = ""
    checkpoint_frac: float = 0.0
    # v2.8 (takeover): the builder works first, without the in-loop review, within takeover_first_step_limit commands
    # and takeover_first_wall_sec; then the senior reviewer judges the result once, and on "fix" this model takes over
    # in the same container with the problems and the builder's notes (takeover_step_limit commands). 2026-11-02 dt1:
    # the v2.7 in-loop review broke 4 tasks GLM alone solved; its verdict said "fix" on 23 of 29 failed runs.
    takeover_model: str = ""
    takeover_first_step_limit: int = 50
    takeover_first_wall_sec: int = 1500
    takeover_step_limit: int = 60
    # v2.9: when the judge lists at most this many problems on a submitted build, the builder fixes them first (one
    # attempt of takeover_fix_step_limit commands) and the judge looks again; only a second "fix" hands over (0 = off).
    # dv8: three of five takeovers followed a one- or two-problem verdict on work a GLM fix might settle for cents.
    takeover_fix_first_max_problems: int = 0
    # v3.1 context engineering: spec_model turns the task into a checklist that the builder, judge and takeover all
    # see ("" = off); repo_map adds a two-level file map and language counts to the starting listing; handoff makes
    # the builder leave /tmp/HANDOFF.md (works / fails / files) that the takeover reads instead of a long digest.
    # v3.3: the takeover's budget is renewed only while its work changes: every progress_block commands the work
    # folder is fingerprinted; progress_patience blocks in a row without change end the attempt (0 = off).
    # v3.4: reasoning effort of the takeover model ("" = its configured default). 2026-11-02: output is 51% of Opus
    # spend in v2.8 (1.28M output tokens, $25.67 of $50.53 over 905 takeover+judge calls), mostly reasoning.
    takeover_effort: str = ""
    progress_block: int = 0
    progress_patience: int = 2
    spec_model: str = ""
    repo_map: bool = False
    handoff: bool = False
    takeover_fix_step_limit: int = 20
    senior_fix_model: str = ""
    senior_fix_step_limit: int = 25
    helper_policy: str = "laya"
    helper_step_limit: int = 30
    max_delegations: int = 12
    max_parallel_helpers: int = 3
    # policy "d1": Liquid AI's d1-2B served from its own venv; revision pinned (it ships its own model code)
    d1_python: str = ".venv-d1/bin/python"
    d1_model: str = "LiquidAI/d1-2B"
    d1_revision: str = "97c57047d5852d77e02dfac211f8768aff77a8b2"
    lead_reviews: int = 0
    # Ablation switches for prompt features (all on by default)
    team_roster: bool = True       # TEAM roster lines in each worker's first prompt
    recency_reminder: bool = True  # step + one-command rule restated at the end of every call
    output_guard: bool = True      # cut replies at invented turns / extra command blocks
    env_bootstrap: bool = True     # the starting directory listing shown as already-run output
    # CLI workers: once a conversation outgrows this, older turns are folded into PROGRESS SO FAR.
    # 0 = off. ~32k chars is ~11-13k tokens with CLI overhead (measured 2.7-3.3 chars/token).
    context_budget_chars: int = 32000
    # claude/ workers: CLAUDE_CODE_MAX_OUTPUT_TOKENS per call (0 = CLI default). Droid has no such flag.
    max_output_tokens: int = 8000
    # Submit gate: times per attempt a submit is sent back because the step's check failed (0 = off).
    submit_checks: int = 2
    # CLI workers keep one session per attempt and send only new turns, so the provider caches the prefix.
    # The session restarts from the compacted view (context_budget_chars) once it passes this size. 0 = off.
    session_budget_chars: int = 80000
    # v3.6: each time a session grows by this many chars, the task is restated by appending it (0 = off). v3.5 kept
    # the cache but its takeovers halved (median 9 vs 18.5 calls) and solved 0/14 held-out: v2.8's restart had
    # been restating the task.
    session_nudge_chars: int = 0
    # v3.10: builder exits that end the run without a judge or takeover (e.g. LimitsExceeded: the builder used its
    # whole command budget; those takeovers solved 0/4 on dev and 0/4 on held-out). Empty = off.
    takeover_skip_exits: list[str] = field(default_factory=list)
    # v4 A1 (verified done): the builder leaves a check script, one check per requirement (CHECKS_NOTE); the harness
    # runs it before the judge, and the judge decides from the task next to the script and its output. Dev
    # 2026-11-18: v2.8's judge shipped 1-2 of 12 runs, handing 21 of 24 to the Opus takeover. Off = v2.8.
    verified_done: bool = False
    checks_timeout_sec: int = 600
    # v4: a judge that runs out of turns without a verdict says "fix" (was "ship": phase2/review.py, red team
    # 2026-11-18). Off = v2.8 behaviour.
    judge_out_of_turns_fix: bool = False
    # v4 B1: list which common tools exist in the container at the start (7% of failed dev commands were
    # "command not found"). Off = v2.8.
    tool_probe: bool = False
    max_parallel: int = 3
    worker_step_limit: int = 40
    worker_command_timeout: int = 180
    check_timeout: int = 300
    random_seed: int = 570
    # `droid/` models are billed in Factory Standard Credits: cost_usd = factory_credits x usd.
    # usd None = no USD rate known, so droid calls add 0 to cost_usd and credits are reported alone.
    factory_credit: dict = field(default_factory=lambda: {"usd": None, "source": "", "checked": ""})

    @property
    def credits_usd(self) -> float | None:
        usd = (self.factory_credit or {}).get("usd")
        return None if usd is None else float(usd)

    @property
    def max_route_tier(self) -> int:
        return len(self.tiers) - 2

    def cli_timeout_for(self, model: str) -> int:
        return int((self.cli_timeout_by_model or {}).get(model, self.cli_timeout_sec))

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str | Path | None = None, **overrides) -> HarnessConfig:
    data = json.loads(Path(path).read_text()) if path else {}
    data.update({k: v for k, v in overrides.items() if v is not None})
    known = HarnessConfig.__dataclass_fields__
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    cfg = HarnessConfig(**data)
    # Harbor --ak values may arrive as strings.
    for name, f in known.items():
        value = getattr(cfg, name)
        if f.type in (list, "list") and isinstance(value, str):  # --ak best_of_models=a,b
            setattr(cfg, name, [v.strip() for v in value.split(",") if v.strip()])
        elif f.type in (bool, "bool") and isinstance(value, str):
            setattr(cfg, name, value.strip().lower() in ("1", "true", "yes", "on"))
        elif f.type in (int, float, "int", "float") and isinstance(value, str):
            setattr(cfg, name, (int if f.type in (int, "int") else float)(value))
    from .droid import is_droid

    missing = [m for m in cfg.tiers + [cfg.planner_model] + list(cfg.helper_models.values())
               + [x for x in (cfg.fixer_model, cfg.reviewer_model, cfg.acceptance_model, cfg.senior_reviewer_model) if x]
               if not is_droid(m) and m not in cfg.prices_per_million]
    if missing:
        raise ValueError(f"no price for models: {missing}")
    return cfg
