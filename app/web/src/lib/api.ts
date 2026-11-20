import type { Connections, EvalDetail, EvalSummary, LiveNow, RunSummary, SetupOption, Snapshot } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new ApiError(0, "The Relay server is not reachable. Is app/run.sh running?");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch { /* not JSON */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  setups: () => call<{ setups: SetupOption[]; default: string }>("/api/setups"),
  history: () => call<{ runs: RunSummary[] }>("/api/history"),
  run: (job: string) => call<Snapshot>(`/api/runs/${encodeURIComponent(job)}`),
  chat: (text: string, setup: string) =>
    call<{ job: string; id: string }>("/api/chat", { method: "POST", body: JSON.stringify({ text, setup }) }),
  rerun: (source_job: string, setup: string) =>
    call<{ job: string; task: string }>("/api/rerun", { method: "POST", body: JSON.stringify({ source_job, setup }) }),
  stop: (job: string) => call<{ done: string[] }>(`/api/runs/${encodeURIComponent(job)}/stop`, { method: "POST" }),
  connections: (setup: string, fresh = false) =>
    call<Connections>(`/api/connections?setup=${encodeURIComponent(setup)}${fresh ? "&fresh=true" : ""}`),
  live: () => call<LiveNow>("/api/live"),
  evaluations: () => call<{ evaluations: EvalSummary[] }>("/api/benchmarks"),
  evaluation: (prefix: string) => call<EvalDetail>(`/api/benchmarks/${encodeURIComponent(prefix)}`),
  streamUrl: (job: string) => `/api/runs/${encodeURIComponent(job)}/stream`,
};
