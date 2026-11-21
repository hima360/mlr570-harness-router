// Typed mirror of the server's normalized events (app/server/normalize.py) and API payloads.

export type Tone = "info" | "good" | "bad" | "warn" | "handoff";
export type PhaseKind =
  | "plan" | "strategy" | "spec" | "build" | "fix" | "checks" | "review" | "takeover" | "helper";
export type PhaseStatus = "running" | "done" | "failed";

export interface ModelInfo {
  id: string; label: string; provider: string; provider_label: string; backend: string; role?: string;
}

export interface Phase {
  id: string; kind: PhaseKind; step: string | null; model: string; label: string; title: string;
  started: number; ended: number | null; status: PhaseStatus;
  calls: number; input: number; cached: number; output: number; cost_usd: number;
}

export interface UsageRow extends ModelInfo {
  input: number; cached: number; output: number; cost_usd: number; calls: number;
}
export interface UsageTotal { input: number; cached: number; output: number; cost_usd: number; calls: number; }

export interface Working {
  phase_id: string; kind: PhaseKind; step: string | null; model: string; label: string; verb: string; since: number;
}

interface Base { seq: number; t: number; }
export type RunEvent =
  | (Base & { type: "run_config"; policy: string; models: ModelInfo[]; verified_done: boolean; judge: string; takeover: string })
  | (Base & { type: "plan"; steps: { id: string; goal: string; depends_on: string[] }[]; planner: string; planner_model: string })
  | (Base & { type: "phase_start"; phase: Phase })
  | (Base & { type: "phase_end"; phase_id: string; status: PhaseStatus; ended: number; note: string })
  | (Base & {
      type: "model_call"; phase_id: string; step: string | null; role: string; model: string; label: string; ms: number;
      input: number; cached: number; output: number; cost_usd: number; cost_source: string; reply: string; session: string;
    })
  | (Base & { type: "call_failed"; phase_id: string | null; model: string; label: string; role: string; kind: string; error: string; will_retry: boolean })
  | (Base & { type: "activity"; phase_id: string | null; text: string })
  | (Base & { type: "checks"; step: string; passes: number; fails: number; found: boolean; exit: string; seconds: number; output: string })
  | (Base & { type: "review"; step: string; model: string; label: string; verdict: string; problems: string[]; round: number; error: string })
  | (Base & { type: "step_end"; step: string; passed: boolean; attempts: number; summary: string; files: string[]; exit: string })
  | (Base & { type: "narration"; text: string; tone: Tone; model: string | null })
  | (Base & { type: "usage"; by_model: UsageRow[]; total: UsageTotal })
  | (Base & { type: "working"; now: Working[] })
  | (Base & { type: "run_done"; escalations: number; failed_steps: number; policy: string })
  | (Base & {
      type: "result"; reward: number | null; tests: number | null; grader: boolean; exception: string;
      started_at: string | null; finished_at: string | null; harbor_cost_usd: number | null;
    });

// Stream-only control messages (not part of a recorded trace).
export type StreamMsg =
  | { type: "reset"; run: RunSummary; instruction: string }
  | { type: "status"; status: string; detail: string }
  | { type: "clock"; t0: number }
  | { type: "end" }
  | RunEvent;

export type RunStatus = "starting" | "running" | "passed" | "failed" | "finished" | "error" | "stopped" | "incomplete";

export interface RunSummary {
  job: string; kind: "chat" | "rerun" | "benchmark"; title: string; setup: string; setup_label: string; task: string;
  prefix: string; repeat: number; status: RunStatus; reward: number | null; started: number; exception: string;
  grader: boolean; policy: string; config: string;
}

export interface Snapshot {
  run: RunSummary; t0: number | null; events: RunEvent[]; trial: string | null; instruction: string;
}

export interface SetupOption { id: string; label: string; blurb: string; }

export interface ConnectionItem {
  id: string; name: string; status: "green" | "amber" | "red"; detail: string; role?: string; provider?: string;
  five_hour?: number; weekly?: number; last_ok?: number | null; last_fail?: number | null;
}
export interface Connections { setup: string; overall: "green" | "amber" | "red"; items: ConnectionItem[]; checked: number; }

export interface EvalSummary { prefix: string; runs: number; setups: string[]; setup_labels: string[]; updated: number; }
export interface EvalSetup {
  setup: string; label: string; runs: number; valid: number; passed: number; mean_tests: number | null; usd: number;
  usd_per_solve: number | null; tokens: number; minutes_per_run: number | null;
}
export interface EvalRun {
  job: string; setup: string; setup_label: string; repeat: number; status: string; reward: number | null;
  tests: number | null; usd: number; tokens: number; minutes: number; exception: string; reviews: number; models: string;
}
export interface EvalDetail {
  prefix: string; setups: EvalSetup[]; tasks: { task: string; runs: EvalRun[] }[]; plan: Record<string, unknown>; error?: string;
}

export interface LiveJob {
  job: string; kind: "chat" | "rerun" | "benchmark"; prefix: string; setup: string; setup_label: string; task: string;
  title: string; started: number; elapsed: number; stage: "starting" | "running";
  working: { label: string; model: string; verb: string; kind: string; since: number }[];
  phase: string; tokens: number; cost_usd: number;
}
export interface LiveBatch {
  prefix: string; total: number; done: number; running: number; queued: number; eta: number | null; usd: number;
  setups: { setup: string; label: string; runs: number; passed: number; usd: number }[]; error?: string;
}
export interface LiveNow {
  running: LiveJob[]; batches: LiveBatch[]; quota: { five_hour: number; weekly: number } | null;
  quota_state: "ok" | "pending" | "unreadable"; checked: number;
}
