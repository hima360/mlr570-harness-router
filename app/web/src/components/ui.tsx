import type { ReactNode } from "react";
import {
  CircleCheck, CircleX, ClipboardCheck, Hammer, LoaderCircle, Route, ScanEye, ShieldCheck, Sparkles, TriangleAlert, Wrench,
} from "lucide-react";
import { modelHue } from "../lib/format";
import type { PhaseKind } from "../lib/types";

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="logo-mark">
      <rect width="32" height="32" rx="8" className="logo-bg" />
      <rect x="6" y="13" width="13" height="6" rx="3" className="logo-a" />
      <rect x="13" y="13" width="13" height="6" rx="3" className="logo-b" />
    </svg>
  );
}

export function ModelChip({ model, label, size = "md" }: { model: string; label: string; size?: "sm" | "md" }) {
  if (!label) return null;
  return (
    <span className={`model-chip hue-${modelHue(model || label)} size-${size}`}>
      <span className="model-dot" aria-hidden="true" />
      {label}
    </span>
  );
}

export function StatusDot({ status, label }: { status: "green" | "amber" | "red" | "grey"; label?: string }) {
  return <span className={`status-dot s-${status}`} role="img" aria-label={label ?? status} />;
}

const RUN_STATUS: Record<string, { label: string; cls: string }> = {
  starting: { label: "Starting", cls: "live" },
  running: { label: "Running", cls: "live" },
  passed: { label: "Solved", cls: "good" },
  failed: { label: "Not solved", cls: "bad" },
  finished: { label: "Finished", cls: "good" },
  error: { label: "Error", cls: "bad" },
  stopped: { label: "Stopped", cls: "muted" },
  incomplete: { label: "Incomplete", cls: "muted" },
};

export function RunStatusBadge({ status }: { status: string }) {
  const s = RUN_STATUS[status] ?? { label: status || "Unknown", cls: "muted" };
  return (
    <span className={`badge b-${s.cls}`}>
      {s.cls === "live" ? <span className="pulse" aria-hidden="true" /> : null}
      {s.label}
    </span>
  );
}

export function Badge({ tone = "muted", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return <span className={`badge b-${tone}`} title={title}>{children}</span>;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <LoaderCircle size={size} className="spin" aria-hidden="true" />;
}

export function PhaseIcon({ kind, size = 15 }: { kind: PhaseKind; size?: number }) {
  const p = { size, "aria-hidden": true as const };
  switch (kind) {
    case "build": return <Hammer {...p} />;
    case "fix": return <Wrench {...p} />;
    case "checks": return <ClipboardCheck {...p} />;
    case "review": return <ScanEye {...p} />;
    case "takeover": return <ShieldCheck {...p} />;
    case "plan": return <Route {...p} />;
    default: return <Sparkles {...p} />;
  }
}

export function PhaseStatusIcon({ status }: { status: string }) {
  if (status === "running") return <Spinner />;
  if (status === "failed") return <CircleX size={15} className="tone-bad" aria-label="failed" />;
  return <CircleCheck size={15} className="tone-good" aria-label="done" />;
}

export function EmptyState({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty-state">
      {icon ? <div className="empty-icon">{icon}</div> : null}
      <div className="empty-title">{title}</div>
      {children ? <div className="empty-body">{children}</div> : null}
    </div>
  );
}

export function ErrorBox({ title = "Something went wrong", message, action }: { title?: string; message: string; action?: ReactNode }) {
  return (
    <div className="error-box" role="alert">
      <TriangleAlert size={18} aria-hidden="true" />
      <div>
        <div className="error-title">{title}</div>
        <div className="error-msg">{message}</div>
        {action ? <div className="error-action">{action}</div> : null}
      </div>
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => <div key={i} className="sk-line" style={{ width: `${88 - i * 14}%` }} />)}
    </div>
  );
}

export function Stat({ label, value, sub, mono = true }: { label: string; value: ReactNode; sub?: ReactNode; mono?: boolean }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value${mono ? " mono" : ""}`}>{value}</div>
      {sub ? <div className="stat-sub">{sub}</div> : null}
    </div>
  );
}
