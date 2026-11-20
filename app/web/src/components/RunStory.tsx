import { createContext, useContext, useState } from "react";
import { ChevronDown, ChevronRight, Clock, Coins, FileText, Cpu } from "lucide-react";
import type { RunState } from "../lib/runState";
import type { RunSummary } from "../lib/types";
import { fmtDuration, fmtPct, fmtTokens, fmtUSD, modelHue } from "../lib/format";
import { Badge, ModelChip, Spinner } from "./ui";

/** Everything a run view knows right now; the chat's tool UI and the task panel both read it. */
export interface RunCtx {
  summary: RunSummary | null;
  state: RunState;
  clock: number;          // seconds since the trace started, at the moment being shown
  live: boolean;
  status: string;
  statusDetail: string;
  recording: boolean;
}

export const RunContext = createContext<RunCtx | null>(null);
export const useRunCtx = () => useContext(RunContext);

/** The assistant's side of a run: plain-language lines as it happens, then the result card. */
export function RunStory() {
  const ctx = useRunCtx();
  if (!ctx) return null;
  const { state, live, status, statusDetail, clock } = ctx;
  const starting = live && state.count === 0;
  return (
    <div className="story">
      {starting ? (
        <div className="story-line tone-info current">
          <Spinner />
          <span>{status === "starting" ? "Harbor is building the task container. The first model call follows." : "Connecting to the run..."}</span>
        </div>
      ) : null}
      <ol className="story-lines" aria-live="polite">
        {state.lines.map((l) => (
          <li key={l.seq} className={`story-line tone-${l.tone}`}>
            <span className={`story-marker hue-${l.model ? modelHue(l.model) : "none"}`} aria-hidden="true" />
            <span className="story-text">{l.text}</span>
            <span className="story-t mono">{fmtDuration(l.t)}</span>
          </li>
        ))}
      </ol>
      {state.working.length && !state.result ? (
        <div className="story-line current" aria-live="polite">
          <Spinner />
          <span>
            {state.working.map((w) => (
              <span key={w.phase_id} className="working-inline">
                <ModelChip model={w.model} label={w.label} size="sm" /> {w.verb}
                <span className="mono muted"> {fmtDuration(clock - w.since)}</span>
              </span>
            ))}
          </span>
        </div>
      ) : null}
      {state.harnessDone && !state.result && live ? (
        <div className="story-line current"><Spinner /><span>Harbor's verifier is checking the result.</span></div>
      ) : null}
      {!live && !state.result && (status === "error" || status === "stopped") ? (
        <div className="story-line tone-bad"><span className="story-marker hue-none" />
          <span className="story-text">Run {status}{statusDetail ? `: ${statusDetail}` : "."}</span></div>
      ) : null}
      {state.result ? <ResultCard /> : null}
    </div>
  );
}

export function ResultCard() {
  const ctx = useRunCtx();
  const [open, setOpen] = useState(false);
  if (!ctx?.state.result) return null;
  const { state, summary } = ctx;
  const r = state.result!;
  const graded = r.grader;
  const solved = r.reward === 1;
  const verdict = r.exception && r.reward === null ? "Run error"
    : !graded ? "Finished" : solved ? "Solved" : "Not solved";
  const tone = r.exception && r.reward === null ? "bad" : !graded ? "neutral" : solved ? "good" : "bad";
  const started = r.started_at ? Date.parse(r.started_at) : NaN;
  const finished = r.finished_at ? Date.parse(r.finished_at) : NaN;
  const wall = Number.isFinite(started) && Number.isFinite(finished) ? (finished - started) / 1000 : state.lastT;
  const t = state.usage.total;
  return (
    <section className={`result-card rc-${tone}`} aria-label="Run result">
      <header className="rc-head">
        <div>
          <div className="rc-eyebrow">{graded ? "Harbor verifier" : "No benchmark grader"}</div>
          <h3 className="rc-title">{verdict}</h3>
        </div>
        <div className="rc-badges">
          {graded ? <Badge tone={solved ? "good" : "bad"}>reward {r.reward ?? "n/a"}</Badge> : <Badge tone="muted">chat task</Badge>}
          {graded && r.tests !== null ? <Badge tone="muted">tests passed {fmtPct(r.tests)}</Badge> : null}
          {ctx.recording ? <Badge tone="rec">recording</Badge> : null}
        </div>
      </header>
      {state.summary ? <p className="rc-summary">{state.summary}</p> : null}
      {r.exception ? <p className="rc-exc mono">{r.exception}</p> : null}
      {state.files.length ? (
        <div className="rc-files">
          <div className="rc-label"><FileText size={14} aria-hidden="true" /> Files the builder reported</div>
          <ul>{state.files.map((f) => <li key={f} className="mono">{f}</li>)}</ul>
        </div>
      ) : null}
      <div className="rc-stats">
        <div><Cpu size={14} aria-hidden="true" /><span className="mono">{fmtTokens(t.input + t.output)}</span> tokens
          <span className="muted"> ({fmtTokens(t.cached)} cached)</span></div>
        <div><Coins size={14} aria-hidden="true" /><span className="mono">{fmtUSD(t.cost_usd)}</span> estimated API cost</div>
        <div><Clock size={14} aria-hidden="true" /><span className="mono">{fmtDuration(wall)}</span> wall time</div>
      </div>
      {state.usage.by_model.length ? (
        <button className="rc-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Who did what
        </button>
      ) : null}
      {open ? (
        <table className="mini-table">
          <thead><tr><th>Model</th><th>Calls</th><th>In</th><th>Cached</th><th>Out</th><th>Est. cost</th></tr></thead>
          <tbody>
            {state.usage.by_model.map((m) => (
              <tr key={m.id}>
                <td><ModelChip model={m.id} label={m.label} size="sm" /></td>
                <td className="mono">{m.calls}</td><td className="mono">{fmtTokens(m.input)}</td>
                <td className="mono">{fmtTokens(m.cached)}</td><td className="mono">{fmtTokens(m.output)}</td>
                <td className="mono">{fmtUSD(m.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {!graded && summary?.kind === "chat" ? (
        <p className="rc-foot muted">Chat tasks have no benchmark tests, so Harbor's verifier always passes them. Judge the work from the summary and the review.</p>
      ) : null}
    </section>
  );
}
