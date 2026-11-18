// Fold normalized run events into the state every view renders (chat narration, task panel, cost meter).
// Pure: the replay player recomputes it for any position, the live stream applies events as they arrive.
import type {
  ModelInfo, Phase, RunEvent, Tone, UsageRow, UsageTotal, Working,
} from "./types";

export interface Call {
  seq: number; t: number; model: string; label: string; role: string; ms: number;
  input: number; cached: number; output: number; cost_usd: number; reply: string; failed?: string;
}
export interface PhaseView extends Phase { calls_list: Call[]; notes: string[]; endNote: string; }
export interface Line { seq: number; t: number; text: string; tone: Tone; model: string | null; }
export interface StepView { id: string; goal: string; status: "pending" | "running" | "done" | "failed"; phases: string[]; }

export interface RunState {
  models: ModelInfo[];
  policy: string;
  judge: string;
  steps: StepView[];
  phases: Record<string, PhaseView>;
  phaseOrder: string[];
  lines: Line[];
  usage: { by_model: UsageRow[]; total: UsageTotal };
  working: Working[];
  checks: { passes: number; fails: number; output: string; ok: boolean } | null;
  reviews: { label: string; verdict: string; problems: string[]; round: number }[];
  summary: string;
  files: string[];
  harnessDone: boolean;
  result: Extract<RunEvent, { type: "result" }> | null;
  lastT: number;
  count: number;
}

export const emptyUsage = { by_model: [] as UsageRow[], total: { input: 0, cached: 0, output: 0, cost_usd: 0, calls: 0 } };

export function initialState(): RunState {
  return {
    models: [], policy: "", judge: "", steps: [], phases: {}, phaseOrder: [], lines: [], usage: emptyUsage,
    working: [], checks: null, reviews: [], summary: "", files: [], harnessDone: false, result: null, lastT: 0, count: 0,
  };
}

function ensureStep(s: RunState, id: string | null): StepView | null {
  if (!id) return null;
  let st = s.steps.find((x) => x.id === id);
  if (!st) {
    st = { id, goal: "", status: "pending", phases: [] };
    s.steps = [...s.steps, st];
  }
  return st;
}

/** Apply one event. Returns a new state object (shallow copies where changed) so React re-renders. */
export function apply(prev: RunState, ev: RunEvent): RunState {
  const s: RunState = { ...prev, count: prev.count + 1, lastT: Math.max(prev.lastT, ev.t) };
  switch (ev.type) {
    case "run_config":
      s.models = ev.models; s.policy = ev.policy; s.judge = ev.judge;
      break;
    case "plan":
      s.steps = ev.steps.map((x) => ({ id: x.id, goal: x.goal, status: "pending", phases: [] }));
      break;
    case "phase_start": {
      const p: PhaseView = { ...ev.phase, calls_list: [], notes: [], endNote: "" };
      s.phases = { ...s.phases, [p.id]: p };
      s.phaseOrder = [...s.phaseOrder, p.id];
      const st = ensureStep(s, p.step);
      if (st) {
        s.steps = s.steps.map((x) => (x.id === st.id ? { ...x, status: "running", phases: [...x.phases, p.id] } : x));
      }
      break;
    }
    case "phase_end": {
      const p = s.phases[ev.phase_id];
      if (p) s.phases = { ...s.phases, [p.id]: { ...p, status: ev.status, ended: ev.ended, endNote: ev.note } };
      break;
    }
    case "model_call": {
      const p = s.phases[ev.phase_id];
      if (p) {
        const call: Call = {
          seq: ev.seq, t: ev.t, model: ev.model, label: ev.label, role: ev.role, ms: ev.ms, input: ev.input,
          cached: ev.cached, output: ev.output, cost_usd: ev.cost_usd, reply: ev.reply,
        };
        s.phases = {
          ...s.phases,
          [p.id]: {
            ...p, calls: p.calls + 1, input: p.input + ev.input, cached: p.cached + ev.cached, output: p.output + ev.output,
            cost_usd: p.cost_usd + ev.cost_usd, calls_list: [...p.calls_list, call],
          },
        };
      }
      break;
    }
    case "call_failed": {
      const p = ev.phase_id ? s.phases[ev.phase_id] : undefined;
      if (p) {
        s.phases = { ...s.phases, [p.id]: { ...p, notes: [...p.notes, `${ev.label} call failed (${ev.kind})${ev.will_retry ? ", retried" : ""}`] } };
      }
      break;
    }
    case "activity": {
      const p = ev.phase_id ? s.phases[ev.phase_id] : undefined;
      if (p) s.phases = { ...s.phases, [p.id]: { ...p, notes: [...p.notes, ev.text] } };
      break;
    }
    case "checks":
      s.checks = { passes: ev.passes, fails: ev.fails, output: ev.output, ok: ev.exit === "0" && ev.fails === 0 && ev.found };
      break;
    case "review":
      s.reviews = [...s.reviews, { label: ev.label, verdict: ev.verdict, problems: ev.problems, round: ev.round }];
      break;
    case "step_end":
      s.steps = s.steps.map((x) => (x.id === ev.step ? { ...x, status: ev.passed ? "done" : "failed" } : x));
      if (ev.summary) s.summary = ev.summary;
      if (ev.files.length) s.files = Array.from(new Set([...s.files, ...ev.files]));
      break;
    case "narration":
      s.lines = [...s.lines, { seq: ev.seq, t: ev.t, text: ev.text, tone: ev.tone, model: ev.model }];
      break;
    case "usage":
      s.usage = { by_model: ev.by_model, total: ev.total };
      break;
    case "working":
      s.working = ev.now;
      break;
    case "run_done":
      s.harnessDone = true;
      break;
    case "result":
      s.result = ev;
      s.working = [];
      break;
  }
  return s;
}

export function reduce(events: RunEvent[], upto = events.length): RunState {
  let s = initialState();
  for (let i = 0; i < upto && i < events.length; i++) s = apply(s, events[i]);
  return s;
}
