import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import type { PhaseView, RunState } from "../lib/runState";
import { fmtAgo, fmtDuration, fmtTokens, fmtUSD } from "../lib/format";
import { usePoll } from "../lib/hooks";
import { api } from "../lib/api";
import { EmptyState, ErrorBox, ModelChip, PhaseIcon, PhaseStatusIcon, Skeleton, Spinner, StatusDot } from "./ui";
import { useRunCtx } from "./RunStory";

type Tab = "tasks" | "cost" | "connections";

export function TaskPanel({ setup }: { setup: string }) {
  const ctx = useRunCtx();
  const [tab, setTab] = useState<Tab>(ctx ? "tasks" : "connections");
  const tabs: [Tab, string][] = [["tasks", "Tasks"], ["cost", "Cost"], ["connections", "Connections"]];
  return (
    <aside className="panel" aria-label="Run details">
      <NowWorking />
      <div className="tabs" role="tablist" aria-label="Panel sections">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`tab${tab === id ? " active" : ""}`}
            onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
      <div className="panel-body" role="tabpanel">
        {tab === "tasks" ? <TaskList /> : tab === "cost" ? <CostMeter /> : <ConnectionsList setup={setup} />}
      </div>
    </aside>
  );
}

function NowWorking() {
  const ctx = useRunCtx();
  if (!ctx) {
    return (
      <div className="now idle">
        <div className="now-label">Who's working</div>
        <div className="now-main muted">Nobody yet. Start a task to see the models hand work to each other.</div>
      </div>
    );
  }
  const { state, clock, live, status } = ctx;
  const w = state.working;
  const t = state.usage.total;
  let main;
  if (w.length) {
    main = w.map((x) => (
      <div key={x.phase_id} className="now-row">
        <ModelChip model={x.model} label={x.label} />
        <span className="now-verb">{x.verb}</span>
        <span className="now-timer mono">{fmtDuration(clock - x.since)}</span>
      </div>
    ));
  } else if (live && state.count === 0) {
    main = <div className="now-row"><Spinner /><span className="now-verb">{status === "starting" ? "Building the container" : "Connecting"}</span></div>;
  } else if (live && state.harnessDone) {
    main = <div className="now-row"><Spinner /><span className="now-verb">Harbor's verifier is checking</span></div>;
  } else if (state.result) {
    main = <div className="now-row"><span className="now-verb">Done. Nobody is working.</span></div>;
  } else if (live) {
    main = <div className="now-row"><Spinner /><span className="now-verb">Between steps</span></div>;
  } else {
    main = <div className="now-row muted">Nobody at this point of the recording.</div>;
  }
  return (
    <div className={`now${w.length ? " active" : ""}`}>
      <div className="now-label">Who's working{ctx.recording ? <span className="rec-dot"> recording</span> : null}</div>
      <div className="now-main">{main}</div>
      <div className="now-totals">
        <span><span className="mono">{fmtTokens(t.input + t.output)}</span> tokens</span>
        <span><span className="mono">{fmtUSD(t.cost_usd)}</span> est. API cost</span>
        <span><span className="mono">{fmtDuration(state.result ? state.lastT : clock)}</span> elapsed</span>
      </div>
    </div>
  );
}

function TaskList() {
  const ctx = useRunCtx();
  if (!ctx) return <EmptyState title="No run selected">The plan, each phase and the model on it appear here.</EmptyState>;
  const { state } = ctx;
  if (!state.steps.length && !state.phaseOrder.length) {
    return ctx.live ? <Skeleton lines={4} /> : <EmptyState title="Nothing logged yet">This run has no trace events.</EmptyState>;
  }
  const loose = state.phaseOrder.filter((id) => !state.phases[id].step);
  return (
    <div className="tasklist">
      {loose.map((id) => <PhaseRow key={id} p={state.phases[id]} />)}
      {state.steps.map((s) => (
        <section key={s.id} className={`step st-${s.status}`}>
          <header className="step-head">
            <span className="step-id mono">{s.id}</span>
            <span className="step-goal" title={s.goal}>{s.goal || "Step"}</span>
          </header>
          <ol className="phases">
            {s.phases.length ? s.phases.map((id) => <PhaseRow key={id} p={state.phases[id]} />)
              : <li className="phase pending"><span className="muted">Waiting</span></li>}
          </ol>
        </section>
      ))}
      <PhasesLegend state={state} />
    </div>
  );
}

function PhasesLegend({ state }: { state: RunState }) {
  if (!state.checks && !state.reviews.length) return null;
  return (
    <div className="legend">
      {state.reviews.map((r, i) => (
        <div key={i} className={`review-box v-${r.verdict === "ship" ? "ship" : "fix"}`}>
          <div className="review-head">{r.label} verdict: <strong>{r.verdict || "none"}</strong>{r.round > 1 ? ` (round ${r.round})` : ""}</div>
          {r.problems.length ? <ul>{r.problems.map((p, j) => <Problem key={j} text={p} />)}</ul> : null}
        </div>
      ))}
    </div>
  );
}

function Problem({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return <li className={open ? "open" : ""} title={open ? undefined : "Click to read it all"} onClick={() => setOpen(true)}><span>{text}</span></li>;
}

function PhaseRow({ p }: { p: PhaseView }) {
  const ctx = useRunCtx()!;
  const [open, setOpen] = useState(false);
  const end = p.ended ?? (ctx.state.result ? ctx.state.lastT : ctx.clock);
  const hasDetail = p.calls_list.length > 0 || p.notes.length > 0 || (p.kind === "checks" && ctx.state.checks);
  return (
    <li className={`phase ph-${p.status}`}>
      <button className="phase-main" onClick={() => hasDetail && setOpen(!open)} aria-expanded={hasDetail ? open : undefined}
        disabled={!hasDetail}>
        <span className="phase-status"><PhaseStatusIcon status={p.status} /></span>
        <span className="phase-kind"><PhaseIcon kind={p.kind} /> {p.title}</span>
        {p.model ? <ModelChip model={p.model} label={p.label} size="sm" /> : <span />}
        <span className="phase-timer mono">{fmtDuration(end - p.started)}</span>
        {hasDetail ? (open ? <ChevronDown size={14} className="chev" /> : <ChevronRight size={14} className="chev" />) : <span />}
      </button>
      {p.calls ? (
        <div className="phase-meta mono">
          {p.calls} call{p.calls === 1 ? "" : "s"} · {fmtTokens(p.input)} in · {fmtTokens(p.cached)} cached · {fmtTokens(p.output)} out · {fmtUSD(p.cost_usd)}
        </div>
      ) : p.endNote && p.kind !== "takeover" ? <div className="phase-meta">{p.endNote}</div> : null}
      {open ? (
        <div className="phase-detail">
          {p.kind === "checks" && ctx.state.checks ? <pre className="code">{ctx.state.checks.output || "(no output)"}</pre> : null}
          {p.notes.map((n, i) => <div key={i} className="phase-note">{n}</div>)}
          {p.calls_list.map((c) => (
            <div key={c.seq} className="call">
              <div className="call-head mono">
                <span>{fmtDuration(c.t)}</span><span>{(c.ms / 1000).toFixed(1)}s</span>
                <span>{fmtTokens(c.input)}/{fmtTokens(c.cached)}/{fmtTokens(c.output)}</span><span>{fmtUSD(c.cost_usd)}</span>
              </div>
              {c.reply ? <div className="call-reply">{c.reply}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
    </li>
  );
}

export function CostMeter() {
  const ctx = useRunCtx();
  if (!ctx) return <EmptyState title="No run selected">Tokens and estimated API cost per model appear here, live.</EmptyState>;
  const { by_model, total } = ctx.state.usage;
  const max = Math.max(...by_model.map((m) => m.cost_usd), 1e-9);
  return (
    <div className="cost">
      <div className="cost-total">
        <div className="cost-big mono">{fmtUSD(total.cost_usd)}</div>
        <div className="muted">estimated API cost: tokens x list price, not a bill</div>
      </div>
      <div className="cost-grid">
        <div><span className="muted">Input</span><span className="mono">{fmtTokens(total.input)}</span></div>
        <div><span className="muted">Cached</span><span className="mono">{fmtTokens(total.cached)}</span></div>
        <div><span className="muted">Output</span><span className="mono">{fmtTokens(total.output)}</span></div>
        <div><span className="muted">Calls</span><span className="mono">{total.calls}</span></div>
      </div>
      {by_model.length ? by_model.map((m) => (
        <div key={m.id} className="cost-model">
          <div className="cost-model-head">
            <ModelChip model={m.id} label={m.label} />
            <span className="muted small">{m.provider_label} via {m.backend}</span>
            <span className="mono cost-usd">{fmtUSD(m.cost_usd)}</span>
          </div>
          <div className="bar" aria-hidden="true"><div className={`bar-fill hue-${m.provider === "zai" ? "glm" : m.provider === "anthropic" ? "opus" : "other"}`}
            style={{ width: `${Math.max(2, (m.cost_usd / max) * 100)}%` }} /></div>
          <div className="cost-model-tokens mono small">
            {m.calls} calls · {fmtTokens(m.input)} in · {fmtTokens(m.cached)} cached · {fmtTokens(m.output)} out
          </div>
        </div>
      )) : <div className="muted small">No model calls yet.</div>}
      {ctx.state.usage.by_model.some((m) => m.backend === "Factory Droid") ? (
        <p className="muted small">This run billed Factory credits; costs shown are the same tokens at API list price.</p>
      ) : null}
    </div>
  );
}

export function ConnectionsList({ setup }: { setup: string }) {
  const { data, error, loading, refresh } = usePoll(() => api.connections(setup), 60_000, [setup]);
  const [fresh, setFresh] = useState(false);
  const pending = !!data?.items.some((c) => c.detail.startsWith("Reading"));
  useEffect(() => {
    if (!pending) return;
    const id = window.setTimeout(refresh, 4000);
    return () => window.clearTimeout(id);
  }, [pending, data]); // eslint-disable-line react-hooks/exhaustive-deps
  const recheck = async () => {
    setFresh(true);
    try { await api.connections(setup, true); } finally { setFresh(false); refresh(); }
  };
  if (loading && !data) return <Skeleton lines={5} />;
  if (error && !data) return <ErrorBox title="Could not check connections" message={error} action={<button className="btn sm" onClick={refresh}>Try again</button>} />;
  if (!data) return null;
  return (
    <div className="conns">
      <div className="conns-head">
        <span className="muted small">For setup <strong>{setup}</strong>. No model quota is spent on these checks.</span>
        <button className="btn ghost sm" onClick={recheck} disabled={fresh} aria-label="Check again">
          {fresh ? <Spinner size={13} /> : <RefreshCw size={13} />} Check
        </button>
      </div>
      <ul className="conn-list">
        {data.items.map((c) => (
          <li key={c.id} className="conn">
            <StatusDot status={c.status} label={c.status} />
            <div className="conn-body">
              <div className="conn-name">{c.name}{c.role ? <span className="muted small"> · {c.role}</span> : null}</div>
              <div className="conn-detail">{c.detail}</div>
              {c.weekly !== undefined ? (
                <div className="quota">
                  <QuotaBar label="5 h" pct={c.five_hour ?? 0} />
                  <QuotaBar label="week" pct={c.weekly ?? 0} />
                </div>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      <div className="muted small">Checked {fmtAgo(data.checked)}.</div>
    </div>
  );
}

function QuotaBar({ label, pct }: { label: string; pct: number }) {
  const tone = pct >= 80 ? "bad" : pct >= 60 ? "warn" : "good";
  return (
    <div className="qbar">
      <span className="muted small">{label}</span>
      <div className="bar"><div className={`bar-fill q-${tone}`} style={{ width: `${Math.min(100, pct)}%` }} /></div>
      <span className="mono small">{pct.toFixed(0)}%</span>
    </div>
  );
}
