import { useMemo, useState } from "react";
import { ChartColumn, Search } from "lucide-react";
import { api } from "../lib/api";
import { go, usePoll } from "../lib/hooks";
import { fmtAgo, fmtPct, fmtTokens, fmtUSD } from "../lib/format";
import type { EvalDetail, EvalRun } from "../lib/types";
import { Badge, EmptyState, ErrorBox, Skeleton } from "./ui";

export function BenchmarkView({ prefix }: { prefix: string | null }) {
  const list = usePoll(() => api.evaluations(), 30_000, []);
  const [q, setQ] = useState("");
  const evals = (list.data?.evaluations ?? []).filter((e) =>
    !q || e.prefix.includes(q.toLowerCase()) || e.setup_labels.join(" ").toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="bench">
      <nav className="bench-list" aria-label="Evaluations">
        <div className="bench-list-head">
          <h2 className="h-section">Evaluations</h2>
          <label className="search">
            <Search size={14} aria-hidden="true" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter" aria-label="Filter evaluations" />
          </label>
        </div>
        {list.loading && !list.data ? <Skeleton lines={8} /> : null}
        {list.error && !list.data ? <ErrorBox message={list.error} /> : null}
        <ul>
          {evals.map((e) => (
            <li key={e.prefix}>
              <button className={`eval-item${prefix === e.prefix ? " on" : ""}`} onClick={() => go("bench", e.prefix)}
                aria-current={prefix === e.prefix ? "page" : undefined}>
                <span className="eval-prefix mono">{e.prefix}</span>
                <span className="eval-sub">{e.runs} runs · {e.setup_labels.slice(0, 3).join(", ")}{e.setup_labels.length > 3 ? ` +${e.setup_labels.length - 3}` : ""}</span>
                <span className="eval-when muted small">{fmtAgo(e.updated)}</span>
              </button>
            </li>
          ))}
          {list.data && !evals.length ? <li className="muted small pad">No evaluation matches.</li> : null}
        </ul>
      </nav>
      <section className="bench-main">
        {prefix ? <EvaluationDetail prefix={prefix} /> : (
          <EmptyState title="Pick an evaluation" icon={<ChartColumn size={28} />}>
            Each evaluation is a set of Harbor runs named <span className="mono">prefix-setup-task</span>. Open one to compare
            setups, then click any run to replay it step by step with the verifier's result.
          </EmptyState>
        )}
      </section>
    </div>
  );
}

function EvaluationDetail({ prefix }: { prefix: string }) {
  const { data, error, loading, refresh } = usePoll(() => api.evaluation(prefix), 20_000, [prefix]);
  if (loading && !data) return <div className="pad-lg"><Skeleton lines={10} /></div>;
  if (error && !data) return <div className="pad-lg"><ErrorBox message={error} action={<button className="btn sm" onClick={refresh}>Try again</button>} /></div>;
  if (!data) return null;
  if (data.error) return <div className="pad-lg"><ErrorBox title="phase2.results could not read this evaluation" message={data.error} /></div>;
  return (
    <div className="pad-lg eval-detail">
      <header className="eval-head">
        <h1 className="display-sm">Evaluation <span className="mono">{prefix}</span></h1>
        <p className="muted">Pass = Harbor reward 1. Cost = tokens x API list price (estimated, not a bill). Cost per solve = total cost / solves.</p>
      </header>
      <SetupTable data={data} />
      <TaskMatrix data={data} prefix={prefix} />
    </div>
  );
}

function SetupTable({ data }: { data: EvalDetail }) {
  if (!data.setups.length) return <EmptyState title="No finished runs yet">Runs appear here once Harbor writes their result.</EmptyState>;
  const best = Math.min(...data.setups.filter((s) => s.passed > 0 && s.usd_per_solve !== null).map((s) => s.usd_per_solve!));
  const most = Math.max(...data.setups.map((s) => s.passed));
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="sr-only">Results per setup</caption>
        <thead>
          <tr><th>Setup</th><th className="num">Runs</th><th className="num">Solved</th><th className="num">Mean tests</th>
            <th className="num">Total est. cost</th><th className="num">Cost per solve</th><th className="num">Tokens</th><th className="num">Min per run</th></tr>
        </thead>
        <tbody>
          {data.setups.map((s) => (
            <tr key={s.setup}>
              <td><span className="chip-setup">{s.label}</span> <span className="muted mono small">{s.setup !== s.label ? s.setup : ""}</span></td>
              <td className="num mono">{s.runs}{s.valid < s.runs ? <span className="muted"> ({s.runs - s.valid} err)</span> : null}</td>
              <td className="num mono">
                <span className={s.passed === most && most > 0 ? "best" : ""}>{s.passed}/{s.runs}</span>
              </td>
              <td className="num mono">{fmtPct(s.mean_tests)}</td>
              <td className="num mono">{fmtUSD(s.usd)}</td>
              <td className="num mono"><span className={s.usd_per_solve === best ? "best" : ""}>{s.usd_per_solve === null ? "no solve" : fmtUSD(s.usd_per_solve)}</span></td>
              <td className="num mono">{fmtTokens(s.tokens)}</td>
              <td className="num mono">{s.minutes_per_run === null ? "n/a" : s.minutes_per_run.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TaskMatrix({ data, prefix }: { data: EvalDetail; prefix: string }) {
  const setups = useMemo(() => {
    const seen: { setup: string; label: string }[] = [];
    for (const t of data.tasks) for (const r of t.runs) if (!seen.some((s) => s.setup === r.setup)) seen.push({ setup: r.setup, label: r.setup_label });
    return seen;
  }, [data]);
  if (!data.tasks.length) return null;
  return (
    <div className="table-wrap">
      <h2 className="h-section">Runs by task <span className="muted small">(click a run to replay it)</span></h2>
      <table className="table matrix">
        <thead><tr><th>Task</th>{setups.map((s) => <th key={s.setup}>{s.label}</th>)}</tr></thead>
        <tbody>
          {data.tasks.map((t) => (
            <tr key={t.task}>
              <td className="mono task-cell" title={t.task}>{t.task}</td>
              {setups.map((s) => (
                <td key={s.setup}>
                  <div className="run-cells">
                    {t.runs.filter((r) => r.setup === s.setup).sort((a, b) => a.repeat - b.repeat).map((r) => (
                      <RunCell key={r.job} r={r} prefix={prefix} />
                    ))}
                  </div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunCell({ r, prefix }: { r: EvalRun; prefix: string }) {
  const state = r.status !== "done" ? "running" : r.exception && r.reward === null ? "error" : r.reward === 1 ? "pass" : "fail";
  const label = state === "running" ? "running" : state === "error" ? "error" : state === "pass" ? "solved" : "not solved";
  return (
    <button className={`run-cell rc-${state}`} onClick={() => go("bench", prefix, r.job)}
      title={`${r.job}\n${label}${r.tests !== null ? `, tests ${fmtPct(r.tests)}` : ""}, ${fmtUSD(r.usd)}, ${r.minutes.toFixed(1)} min${r.exception ? `\n${r.exception}` : ""}`}
      aria-label={`Replay ${r.job}: ${label}`}>
      <span className="rc-mark" aria-hidden="true" />
      <span className="mono">{state === "running" ? "live" : state === "error" ? "err" : r.tests !== null ? fmtPct(r.tests) : state}</span>
      <span className="mono muted">{fmtUSD(r.usd)}</span>
      {r.reviews ? <Badge tone="muted" title="Reviewed by the judge">rev</Badge> : null}
    </button>
  );
}
