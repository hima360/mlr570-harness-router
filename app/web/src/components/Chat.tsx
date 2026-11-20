import { useEffect, useMemo, useRef, useState } from "react";
import {
  AssistantRuntimeProvider, ComposerPrimitive, MessagePrimitive, ThreadPrimitive, useExternalStoreRuntime,
  type AppendMessage, type ThreadMessageLike,
} from "@assistant-ui/react";
import { ArrowUp, CornerDownLeft } from "lucide-react";
import { api } from "../lib/api";
import { go } from "../lib/hooks";
import type { SetupOption } from "../lib/types";
import { RunStory, useRunCtx } from "./RunStory";
import { ErrorBox, Kbd, Logo, Spinner } from "./ui";

const SUGGESTIONS = [
  "Write a Python CLI that converts a CSV of expenses into a monthly summary table, with tests.",
  "Create a small Flask app with a /health endpoint and a pytest test for it.",
  "Write a bash script that finds the 10 largest files under /app and prints them as a table.",
];

const RUN_PART = "relay_run";

/** The chat thread (assistant-ui). One thread = one task = one Harbor run. */
export function ChatThread({ job, instruction, setups, setup, onSetup }: {
  job: string | null; instruction: string; setups: SetupOption[]; setup: string; onSetup: (s: string) => void;
}) {
  const ctx = useRunCtx();
  const [pending, setPending] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const messages = useMemo<ThreadMessageLike[]>(() => {
    if (!job) return [];
    const done = !!ctx && !ctx.live;
    return [
      { id: `u-${job}`, role: "user", content: [{ type: "text", text: instruction || "(task text not found)" }] },
      {
        id: `a-${job}`, role: "assistant",
        status: done ? { type: "complete", reason: "stop" } : { type: "running" },
        content: [{ type: "tool-call", toolCallId: job, toolName: RUN_PART, args: { job }, result: done ? { done: true } : undefined }],
      },
    ];
  }, [job, instruction, ctx?.live]); // eslint-disable-line react-hooks/exhaustive-deps

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages,
    isRunning: false,
    convertMessage: (m) => m,
    onNew: async (m: AppendMessage) => {
      const text = m.content.map((p) => (p.type === "text" ? p.text : "")).join("\n").trim();
      if (text) { setError(null); setPending(text); }
    },
  });

  const start = async () => {
    if (!pending || starting) return;
    setStarting(true); setError(null);
    try {
      const r = await api.chat(pending, setup);
      setPending(null);
      go("run", r.job);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const chosen = setups.find((s) => s.id === setup);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="thread">
        <ThreadPrimitive.Viewport className="thread-viewport">
          <ThreadPrimitive.Empty>
            <Welcome onPick={(t) => { setError(null); setPending(t); }} />
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
          <ThreadPrimitive.ViewportFooter className="thread-footer">
            {pending ? (
              <ConfirmStart text={pending} setupLabel={chosen?.label ?? setup} blurb={chosen?.blurb ?? ""} busy={starting}
                onStart={start} onCancel={() => setPending(null)} />
            ) : null}
            {error ? <ErrorBox title="The run did not start" message={error} /> : null}
            <ComposerPrimitive.Root className="composer">
              <ComposerPrimitive.Input className="composer-input" rows={1} autoFocus={!job}
                placeholder={job ? "Give Relay another task (starts a new run)" : "Describe a task in plain language"}
                aria-label="Task for Relay" />
              <div className="composer-bar">
                <label className="setup-pick">
                  <span className="muted small">Setup</span>
                  <select value={setup} onChange={(e) => onSetup(e.target.value)} aria-label="Harness setup">
                    {setups.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                  </select>
                </label>
                <span className="muted small composer-hint">{chosen?.blurb}</span>
                <ComposerPrimitive.Send className="send-btn" aria-label="Send">
                  <ArrowUp size={18} />
                </ComposerPrimitive.Send>
              </div>
            </ComposerPrimitive.Root>
          </ThreadPrimitive.ViewportFooter>
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="msg msg-user">
      <div className="msg-user-bubble">
        <MessagePrimitive.Parts components={{ Text: ({ text }) => <p className="msg-text">{text}</p> }} />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  const ctx = useRunCtx();
  return (
    <MessagePrimitive.Root className="msg msg-assistant">
      <div className="msg-avatar" aria-hidden="true"><Logo size={26} /></div>
      <div className="msg-body">
        <div className="msg-name">Relay{ctx?.summary ? <span className="muted small"> · {ctx.summary.setup_label}</span> : null}</div>
        <MessagePrimitive.Parts components={{
          Text: ({ text }) => <p className="msg-text">{text}</p>,
          tools: { by_name: { [RUN_PART]: RunStory }, Fallback: () => null },
        }} />
      </div>
    </MessagePrimitive.Root>
  );
}

function Welcome({ onPick }: { onPick: (t: string) => void }) {
  return (
    <div className="welcome">
      <div className="welcome-mark"><Logo size={44} /></div>
      <h1 className="display">What should the relay build?</h1>
      <p className="welcome-sub">
        Describe a task. A cheap model builds it in a fresh container, checks run, a stronger model reviews,
        and takes over only when the work needs it. You see every handoff, token and estimated dollar as it happens.
      </p>
      <div className="suggestions">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="suggestion" onClick={() => onPick(s)}>{s}</button>
        ))}
      </div>
      <p className="muted small">Press <Kbd>Enter</Kbd> to send, <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd> for a new line, <Kbd>?</Kbd> for shortcuts.</p>
    </div>
  );
}

function ConfirmStart({ text, setupLabel, blurb, busy, onStart, onCancel }: {
  text: string; setupLabel: string; blurb: string; busy: boolean; onStart: () => void; onCancel: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="confirm" role="dialog" aria-label="Start a real run">
      <div className="confirm-text">
        <div className="confirm-title">Start a real run with {setupLabel}?</div>
        <div className="confirm-task">{text.length > 220 ? text.slice(0, 220) + "..." : text}</div>
        <div className="muted small">{blurb}. It uses real model quota; the estimated API cost updates live.</div>
      </div>
      <div className="confirm-actions">
        <button className="btn ghost" onClick={onCancel}>Cancel <Kbd>Esc</Kbd></button>
        <button ref={ref} className="btn primary" onClick={onStart} disabled={busy}>
          {busy ? <Spinner /> : <CornerDownLeft size={15} />} Start run
        </button>
      </div>
    </div>
  );
}
