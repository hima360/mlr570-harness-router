"""The evaluation setups: name -> (routing policy, caps[, config overriding the base config]).

Shared by scripts/run_compare.py (which runs them), scripts/supervise.py and the TUI's New run screen.
Single-model baselines and the harness versions run in the same harness, so only the setup differs."""

BASE = {"max_plan_steps": "1", "worker_step_limit": "80", "step_wall_time_sec": "2700",
        "max_interventions": "0", "max_escalations": "0"}
ROUTED = {"max_plan_steps": "5", "worker_step_limit": "40", "step_wall_time_sec": "1800"}
# --long: Terminal-Bench allows hours per task; give the agents room to verify (2026-10-27: agents submitted
# early, sure they were done)
LONG = {"worker_step_limit": "80", "step_wall_time_sec": "3600", "submit_checks": "3"}
SETUPS = {  # name: (policy, caps[, config overriding --config])
    "haiku": ("fixed:0", BASE),
    "sonnet": ("fixed:1", BASE),
    "opus": ("fixed:2", BASE),
    "routed": ("laya", ROUTED),
    "split": ("laya", {**ROUTED, "merge_chains": "false"}),
    "planner": ("planner", ROUTED),
    "levers": ("planner", {**ROUTED, **{"worker_step_limit": "80", "step_wall_time_sec": "3600", "submit_checks": "3"}},
               "configs/phase2-droid-levers.json"),
    # Lead + helpers: the baseline's single agent and budget, plus `delegate` to cheaper helpers
    # (configs/phase2-droid-lead.json = the eval config + helper_models). Only delegation differs from opus/sonnet.
    "leadopus": ("fixed:2", BASE, "configs/phase2-droid-lead.json"),
    "leadsonnet": ("fixed:1", BASE, "configs/phase2-droid-lead.json"),
    # Sonnet builds, Opus reviews on submit (configs/phase2-droid-review.json = the eval config + senior reviewer).
    "review": ("fixed:1", BASE, "configs/phase2-droid-review.json"),
    # v2.5: v2 plus measure-the-targets (worker) and evidence-backed review (reviewer).
    "review25": ("fixed:1", BASE, "configs/phase2-droid-review25.json"),
    # The same three with GLM-5.3 Flash as the builder (Factory dropped Sonnet 5.5 on 2026-10-29); Opus reviews.
    "glm": ("fixed:1", BASE, "configs/phase2-droid-glm.json"),
    "glmreview": ("fixed:1", BASE, "configs/phase2-droid-glm-review.json"),
    "glmreview25": ("fixed:1", BASE, "configs/phase2-droid-glm-review25.json"),
    # v2.6: v2.5 plus the clock (time and commands used after every command, wrap up at 70%).
    "glmreview26": ("fixed:1", BASE, "configs/phase2-droid-glm-review26.json"),
}
