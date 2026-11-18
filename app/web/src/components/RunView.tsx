import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Pause, Play, RotateCcw, SkipBack, SkipForward, Square } from "lucide-react";
import { api } from "../lib/api";
import { go, SPEEDS, useFold, useNow, usePlayer, useRun, type RunData } from "../lib/hooks";
import { fmtClock, fmtDuration, fmtTokens, fmtUSD } from "../lib/format";
import type { RunState } from "../lib/runState";
import type { SetupOption } from "../lib/types";
import { ChatThread } from "./Chat";
import { RunContext, RunStory, type RunCtx } from "./RunStory";
import { TaskPanel } from "./TaskPanel";
import { RerunButton } from "./Rerun";
import { Badge, ErrorBox, Kbd, RunStatusBadge, Skeleton, Spinner } from "./ui";

/** Any run: live (SSE) while it runs, a recording with a player once it has finished. */
export function RunView({ job, setups, setup, onSetup, backTo }: {
  job: string; setups: SetupOption[]; setup: string; onSetup: (s: string) => void; backTo?: string[];
}) {
  const run = useRun(job);
  if (run.loading) return <Shell><div className="center-pad"><Skeleton lines={6} /></div><aside className="panel"><Skeleton lines={8} /></aside></Shell>;
  if (run.error || !run.summary) {
    return (
      <Shell>
        <div className="center-pad">
          <ErrorBox title="Could not open this run" message={run.error ?? "Unknown run."}
            action={<button className="btn" onClick={run.reload}>Try again</button>} />
        </div>
      </Shell>
    );
  }
  return run.live ? <LiveRun run={run} setups={setups} setup={setup} onSetup={onSetup} backTo={backTo} />
    : <RecordedRun run={run} setups={setups} setup={setup} onSetup={onSetup} backTo={backTo} />;
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="workspace">{children}</div>;
}

interface Props { run: RunData; setups: SetupOption[]; setup: string; onSetup: (s: string) => void; backTo?: string[]; }

function LiveRun({ run, ...rest }: Props) {
  const state = useFold(run.events);
  const now = useNow(true);
  const clock = run.t0 ? now - run.t0 : state.lastT;
  const ctx: RunCtx = {
    summary: run.summary, state, clock: Math.max(clock, state.lastT), live: true, status: run.status,
    statusDetail: run.statusDetail, recording: false,
  };
  return <RunLayout ctx={ctx} run={run} {...rest} />;
}

function RecordedRun({ run, ...rest }: Props) {
  const isChat = run.summary?.kind === "chat";
  const player = usePlayer(run.events, { startAtEnd: isChat, autoplay: !isChat });
  const ctx: RunCtx = {
    summary: run.summary, state: player.state, clock: player.clock, live: false, status: run.status,
    statusDetail: run.statusDetail, recording: !isChat,
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e)) return;
      if (e.key === " " ) { e.preventDefault(); player.setPlaying(!player.playing); }
      else if (e.key === "ArrowRight") { e.preventDefault(); player.step(1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); player.step(-1); }
      else if (e.key === "]") player.setSpeed(SPEEDS[Math.min(SPEEDS.length - 1, SPEEDS.indexOf(player.speed) + 1)]);
      else if (e.key === "[") player.setSpeed(SPEEDS[Math.max(0, SPEEDS.indexOf(player.speed) - 1)]);
      else if (e.key === "Home") player.seek(0);
      else if (e.key === "End") player.seek(player.total);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [player]);
  return <RunLayout ctx={ctx} run={run} player={player} {...rest} />;
}

export function isTyping(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
}

function RunLayout({ ctx, run, player, setups, setup, onSetup, backTo }: Props & { ctx: RunCtx; player?: ReturnType<typeof usePlayer> }) {
  const s = run.summary!;
  const isChat = s.kind === "chat";
  const panelSetup = setups.some((x) => x.id === s.setup) ? s.setup : setup;
  return (
    <RunContext.Provider value={ctx}>
      <div className="workspace">
        <section className="center">
          <RunHeader ctx={ctx} run={run} backTo={backTo} />
          {isChat ? (
            <ChatThread job={s.job} instruction={run.instruction} setups={setups} setup={setup} onSetup={onSetup} />
          ) : (
            <div className="replay-scroll">
              <div className="replay-col">
                <details className="instruction" open={ctx.state.count < 3}>
                  <summary>Task: <span className="mono">{s.task}</span></summary>
                  <pre className="instruction-text">{run.instruction || "Instruction not recorded."}</pre>
                </details>
                <div className="msg msg-assistant">
                  <div className="msg-body"><RunStory /></div>
                </div>
              </div>
            </div>
          )}
          {player ? <PlayerBar player={player} recording={ctx.recording} /> : null}
        </section>
        <TaskPanel setup={panelSetup} />
      </div>
    </RunContext.Provider>
  );
}

function RunHeader({ ctx, run, backTo }: { ctx: RunCtx; run: RunData; backTo?: string[] }) {
  const s = run.summary!;
  const [stopping, setStopping] = useState(false);
  const [stopErr, setStopErr] = useState<string | null>(null);
  const t = ctx.state.usage.total;
  const canStop = ctx.live && (s.kind === "chat" || s.kind === "rerun");
  const stop = async () => {
    if (!window.confirm("Stop this run? Its container is removed and the run cannot be resumed.")) return;
    setStopping(true); setStopErr(null);
    try { await api.stop(s.job); run.reload(); } catch (e) { setStopErr(e instanceof Error ? e.message : String(e)); }
    finally { setStopping(false); }
  };
  return (
    <header className="run-header">
      <div className="run-title-row">
        {backTo ? <button className="btn ghost icon" onClick={() => go(...backTo)} aria-label="Back"><ArrowLeft size={16} /></button> : null}
        <h2 className="run-title" title={s.title}>{s.title}</h2>
        <RunStatusBadge status={ctx.live ? (run.status || "running") : s.status} />
        {ctx.recording ? <Badge tone="rec" title="A recorded run replayed from its trace">recording</Badge> : null}
        {!s.grader ? <Badge tone="muted" title="Chat tasks have no benchmark tests">no benchmark grader</Badge> : null}
      </div>
      <div className="run-meta">
        <span className="chip-setup">{s.setup_label || s.setup}</span>
        <span className="mono muted small">{s.job}</span>
        <span className="spacer" />
        <span className="meta-num"><span className="mono">{fmtTokens(t.input + t.output)}</span> tokens</span>
        <span className="meta-num"><span className="mono">{fmtUSD(t.cost_usd)}</span> est. cost</span>
        <span className="meta-num"><span className="mono">{fmtDuration(ctx.state.result ? ctx.state.lastT : ctx.clock)}</span></span>
        {canStop ? (
          <button className="btn danger sm" onClick={stop} disabled={stopping}>
            {stopping ? <Spinner size={13} /> : <Square size={13} />} Stop
          </button>
        ) : null}
        {s.kind !== "chat" && !ctx.live ? <RerunButton sourceJob={s.job} task={s.task} /> : null}
      </div>
      {stopErr ? <ErrorBox title="Could not stop the run" message={stopErr} /> : null}
      {ctx.live && run.statusDetail && run.status !== "running" ? <div className="muted small status-detail">{run.statusDetail}</div> : null}
    </header>
  );
}

function PlayerBar({ player, recording }: { player: ReturnType<typeof usePlayer>; recording: boolean }) {
  const marks = useMemo(() => player.total, [player.total]);
  const atEnd = player.pos >= player.total;
  return (
    <div className="player" role="group" aria-label="Replay controls">
      <button className="btn ghost icon" onClick={() => player.step(-1)} aria-label="Previous moment (Left arrow)"><SkipBack size={16} /></button>
      {atEnd ? (
        <button className="btn primary icon" onClick={() => { player.seek(0); player.setPlaying(true); }} aria-label="Replay from the start">
          <RotateCcw size={16} />
        </button>
      ) : (
        <button className="btn primary icon" onClick={() => player.setPlaying(!player.playing)} aria-label={player.playing ? "Pause (Space)" : "Play (Space)"}>
          {player.playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
      )}
      <button className="btn ghost icon" onClick={() => player.step(1)} aria-label="Next moment (Right arrow)"><SkipForward size={16} /></button>
      <span className="mono small player-time">{fmtClock(player.clock)} / {fmtClock(player.duration)}</span>
      <input className="scrub" type="range" min={0} max={marks} value={player.pos}
        onChange={(e) => { player.setPlaying(false); player.seek(Number(e.target.value)); }}
        aria-label="Scrub through the recording" aria-valuetext={`event ${player.pos} of ${player.total}`} />
      <div className="speeds" role="radiogroup" aria-label="Replay speed">
        {SPEEDS.map((sp) => (
          <button key={sp} role="radio" aria-checked={player.speed === sp} className={`speed${player.speed === sp ? " on" : ""}`}
            onClick={() => player.setSpeed(sp)}>{sp}x</button>
        ))}
      </div>
      {recording ? <span className="rec-label"><span className="rec-dot-i" />Recording</span> : <span className="muted small">Replay</span>}
      <span className="player-keys muted small"><Kbd>Space</Kbd> <Kbd>&larr;</Kbd><Kbd>&rarr;</Kbd> <Kbd>[</Kbd><Kbd>]</Kbd></span>
    </div>
  );
}

export type { RunState };
