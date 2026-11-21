import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { go, useNow, usePoll } from "../lib/hooks";
import { fmtDuration, fmtTokens, fmtUSD, modelHue } from "../lib/format";
import type { LiveBatch, LiveJob } from "../lib/types";

/** Sidebar: progress per active evaluation, the GLM quota, and every job whose process is alive. */
export function RunningNow({ current }: { current?: string }) {
  const live = usePoll(() => api.live(), 12_000, []);
  const now = useNow(true);
  const [fetchedAt, setFetchedAt] = useState(() => Date.now() / 1000);
  useEffect(() => { if (live.data) setFetchedAt(Date.now() / 1000); }, [live.data]);
  const d = live.data;
  if (!d) return live.error ? <div className="muted small hist-empty">Live status unavailable.</div> : null;
  const drift = Math.max(0, now - fetchedAt);
  return (
    <div className="hist-group live-group" aria-label="Running now">
      <div className="hist-title live-title">
        Running now{d.running.length ? <span className="live-count">{d.running.length}</span> : null}
      </div>
      <Quota q={d.quota} state={d.quota_state} />
      {d.batches.map((b) => <BatchCard key={b.prefix} b={b} />)}
      {!d.running.length ? <div className="muted small hist-empty">Nothing is running.</div> : null}
      <ul>
        {d.running.map((r) => <LiveItem key={r.job} r={r} drift={drift} on={current === r.job} />)}
      </ul>
    </div>
  );
}

function Quota({ q, state }: { q: { five_hour: number; weekly: number } | null; state: string }) {
  if (!q) return <div className="quota-line muted small">GLM quota: {state === "pending" ? "reading..." : "unreadable"}</div>;
  const tone = (p: number) => (p >= 80 ? "bad" : p >= 60 ? "warn" : "good");
  return (
    <div className="quota-line small" title="Z.ai Coding Plan quota used (GLM)">
      <span className="muted">GLM quota</span>
      <span className={`qpill q-${tone(q.five_hour)}`}><span className="muted">5h</span> <span className="mono">{q.five_hour.toFixed(0)}%</span></span>
      <span className={`qpill q-${tone(q.weekly)}`}><span className="muted">week</span> <span className="mono">{q.weekly.toFixed(0)}%</span></span>
    </div>
  );
}

function BatchCard({ b }: { b: LiveBatch }) {
  if (b.error) return <div className="batch muted small">{b.prefix}: {b.error}</div>;
  const total = Math.max(b.total, 1);
  const eta = b.eta ? new Date(b.eta * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
  return (
    <button className="batch" onClick={() => go("bench", b.prefix)} title={`Open evaluation ${b.prefix}`}>
      <div className="batch-head">
        <span className="mono batch-prefix">{b.prefix}</span>
        <span className="muted small mono">{b.done}/{b.total} done</span>
      </div>
      <div className="batch-bar" aria-hidden="true">
        <span className="bb-done" style={{ width: `${(b.done / total) * 100}%` }} />
        <span className="bb-run" style={{ width: `${(b.running / total) * 100}%` }} />
      </div>
      <div className="batch-meta small">
        <span><span className="mono">{b.running}</span> running</span>
        <span><span className="mono">{b.queued}</span> queued</span>
        <span className="mono">{fmtUSD(b.usd)}</span>
        {eta ? <span className="muted">ETA {eta}</span> : null}
      </div>
      {b.setups.length ? (
        <div className="batch-setups small">
          {b.setups.map((s) => (
            <span key={s.setup}><strong>{s.label}</strong> <span className="mono">{s.passed}/{s.runs}</span></span>
          ))}
        </div>
      ) : null}
    </button>
  );
}

function LiveItem({ r, drift, on }: { r: LiveJob; drift: number; on: boolean }) {
  const w = r.working[0];
  return (
    <li>
      <button className={`hist-item live-item${on ? " on" : ""}`} onClick={() => go("run", r.job)}
        aria-current={on ? "page" : undefined} title={r.job}>
        <span className="hist-dot hd-running" aria-label="running" />
        <span className="hist-text">{r.title}</span>
        <span className="hist-meta live-meta">
          <span className="chip-setup xs">{r.setup_label}</span>
          <span className="mono">{fmtDuration(r.elapsed + drift)}</span>
        </span>
        <span className="hist-meta live-who">
          {r.stage === "starting" ? "building container"
            : w ? <><span className={`who-dot hue-${modelHue(w.model)}`} />{w.label} {w.verb.replace(/^is /, "")}</>
              : r.phase || "between steps"}
        </span>
        <span className="hist-meta mono">{fmtTokens(r.tokens)} tok · {fmtUSD(r.cost_usd)}</span>
      </button>
    </li>
  );
}
