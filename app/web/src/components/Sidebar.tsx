import { ChartColumn, Keyboard, MessageSquare, Monitor, Moon, Plus, Presentation, Sun } from "lucide-react";
import { api } from "../lib/api";
import { go, usePoll } from "../lib/hooks";
import { fmtAgo } from "../lib/format";
import type { RunSummary } from "../lib/types";
import { Kbd, Logo, Skeleton } from "./ui";
import { RunningNow } from "./RunningNow";

export type ThemePref = "system" | "light" | "dark";

export function Sidebar({ route, theme, onTheme, onShortcuts, present, onPresent }: {
  route: string[]; theme: ThemePref; onTheme: (t: ThemePref) => void; onShortcuts: () => void;
  present: boolean; onPresent: () => void;
}) {
  const hist = usePoll(() => api.history(), 5000, []);
  const runs = hist.data?.runs ?? [];
  const chats = runs.filter((r) => r.kind === "chat");
  const reruns = runs.filter((r) => r.kind === "rerun");
  const others = runs.filter((r) => r.kind === "benchmark" && r.status !== "running").slice(0, 15);
  const current = route[0] === "run" ? route[1] : route[0] === "bench" ? route[2] : undefined;
  const nextTheme: Record<ThemePref, ThemePref> = { system: "light", light: "dark", dark: "system" };
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="brand">
        <Logo size={26} />
        <span className="wordmark">Relay</span>
        <span className="brand-sub">MLR570 routed harness</span>
      </div>
      <button className="btn new-chat" onClick={() => go("chat")}>
        <Plus size={16} /> New task <span className="spacer" /><Kbd>N</Kbd>
      </button>
      <div className="nav-links">
        <button className={`nav-link${route[0] === "chat" || (route[0] === "run") ? " on" : ""}`} onClick={() => go("chat")}>
          <MessageSquare size={16} /> Chat
        </button>
        <button className={`nav-link${route[0] === "bench" ? " on" : ""}`} onClick={() => go("bench")}>
          <ChartColumn size={16} /> Benchmarks <span className="spacer" /><Kbd>B</Kbd>
        </button>
      </div>
      <div className="history" aria-label="Run history">
        {hist.loading && !hist.data ? <Skeleton lines={6} /> : null}
        {hist.error && !hist.data ? <div className="muted small pad">History unavailable: {hist.error}</div> : null}
        <RunningNow current={current} />
        <HistoryGroup title="Chats" runs={chats} current={current} empty="Your chats appear here." />
        {reruns.length ? <HistoryGroup title="Reruns" runs={reruns} current={current} /> : null}
        <HistoryGroup title="Recent harness runs" runs={others} current={current} />
      </div>
      <div className="sidebar-foot">
        <button className="btn ghost sm" onClick={() => onTheme(nextTheme[theme])} aria-label={`Theme: ${theme}. Switch theme`}
          title="Theme (T)">
          {theme === "light" ? <Sun size={15} /> : theme === "dark" ? <Moon size={15} /> : <Monitor size={15} />}
          <span className="cap">{theme}</span>
        </button>
        <button className={`btn ghost sm${present ? " on" : ""}`} onClick={onPresent} aria-pressed={present} title="Presentation size (P)">
          <Presentation size={15} /> Present
        </button>
        <button className="btn ghost sm icon" onClick={onShortcuts} aria-label="Keyboard shortcuts (?)"><Keyboard size={15} /></button>
      </div>
    </nav>
  );
}

function HistoryGroup({ title, runs, current, empty }: { title: string; runs: RunSummary[]; current?: string; empty?: string }) {
  return (
    <div className="hist-group">
      <div className="hist-title">{title}</div>
      {!runs.length && empty ? <div className="muted small hist-empty">{empty}</div> : null}
      <ul>
        {runs.map((r) => (
          <li key={r.job}>
            <button className={`hist-item${current === r.job ? " on" : ""}`}
              onClick={() => (r.kind === "benchmark" ? go("bench", r.prefix, r.job) : go("run", r.job))}
              aria-current={current === r.job ? "page" : undefined} title={`${r.job}\n${r.title}`}>
              <span className={`hist-dot hd-${r.status}`} aria-label={r.status} />
              <span className="hist-text">{r.title}</span>
              <span className="hist-meta">{r.setup_label} · {fmtAgo(r.started)}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
