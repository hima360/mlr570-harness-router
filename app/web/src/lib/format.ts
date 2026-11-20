export function fmtTokens(n: number | null | undefined): string {
  const v = n ?? 0;
  if (v < 1000) return String(v);
  if (v < 1_000_000) return `${(v / 1000).toFixed(v < 10_000 ? 1 : 0)}k`;
  return `${(v / 1_000_000).toFixed(v < 10_000_000 ? 2 : 1)}M`;
}

export function fmtUSD(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "n/a";
  if (n === 0) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

export function fmtDuration(sec: number | null | undefined): string {
  const s = Math.max(0, Math.round(sec ?? 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

export function fmtClock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtPct(x: number | null | undefined): string {
  return x === null || x === undefined ? "n/a" : `${Math.round(x * 100)}%`;
}

export function fmtAgo(ts: number | null | undefined): string {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Colour family for a model: GLM (Z.ai) slate blue, Claude terracotta, others violet. */
export function modelHue(modelOrProvider: string): "glm" | "opus" | "other" {
  const m = (modelOrProvider || "").toLowerCase();
  if (m.includes("glm") || m.includes("zai")) return "glm";
  if (m.includes("claude") || m.includes("opus") || m.includes("sonnet") || m.includes("haiku") || m.includes("anthropic")) return "opus";
  return "other";
}

export function isLive(status: string | undefined): boolean {
  return status === "running" || status === "starting";
}
