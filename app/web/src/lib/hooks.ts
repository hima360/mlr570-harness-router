import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { apply, initialState, reduce, type RunState } from "./runState";
import type { RunEvent, RunSummary, StreamMsg } from "./types";
import { isLive } from "./format";

export interface RunData {
  loading: boolean;
  error: string | null;
  summary: RunSummary | null;
  instruction: string;
  events: RunEvent[];
  t0: number | null;
  status: string;
  statusDetail: string;
  live: boolean;
  reload: () => void;
}

/** A run's events: a recorded snapshot, or a live SSE stream while it is running. */
export function useRun(job: string | null): RunData {
  const [data, setData] = useState<Omit<RunData, "reload">>({
    loading: true, error: null, summary: null, instruction: "", events: [], t0: null, status: "", statusDetail: "", live: false,
  });
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!job) return;
    let cancelled = false;
    let es: EventSource | null = null;
    setData((d) => ({ ...d, loading: true, error: null, events: [], summary: null }));
    api.run(job).then((snap) => {
      if (cancelled) return;
      const live = isLive(snap.run.status);
      setData({
        loading: false, error: null, summary: snap.run, instruction: snap.instruction, events: live ? [] : snap.events,
        t0: snap.t0, status: snap.run.status, statusDetail: snap.run.exception, live,
      });
      if (!live) return;
      es = new EventSource(api.streamUrl(job));
      let buffer: RunEvent[] = [];
      let flush: number | null = null;
      const push = () => {
        flush = null;
        const batch = buffer;
        buffer = [];
        if (batch.length) setData((d) => ({ ...d, events: [...d.events, ...batch] }));
      };
      es.onmessage = (m) => {
        const msg = JSON.parse(m.data) as StreamMsg;
        switch (msg.type) {
          case "reset":
            buffer = [];
            setData((d) => ({ ...d, events: [], summary: msg.run, instruction: msg.instruction || d.instruction }));
            break;
          case "status":
            setData((d) => ({ ...d, status: msg.status, statusDetail: msg.detail, live: isLive(msg.status) }));
            break;
          case "clock":
            setData((d) => ({ ...d, t0: msg.t0 }));
            break;
          case "end":
            es?.close();
            push();
            api.run(job).then((s) => !cancelled && setData((d) => ({ ...d, summary: s.run, live: false, status: s.run.status })))
              .catch(() => undefined);
            break;
          default:
            buffer.push(msg);
            if (flush === null) flush = window.setTimeout(push, 60);
        }
      };
      es.onerror = () => {
        // EventSource reconnects by itself; the server replays from the start after a `reset`.
        setData((d) => ({ ...d, statusDetail: d.live ? "Reconnecting to the run stream..." : d.statusDetail }));
      };
    }).catch((e: unknown) => {
      if (cancelled) return;
      const msg = e instanceof ApiError ? (e.status === 404 ? "This run no longer exists in jobs/." : e.message) : String(e);
      setData((d) => ({ ...d, loading: false, error: msg }));
    });
    return () => { cancelled = true; es?.close(); };
  }, [job, nonce]);

  return { ...data, reload };
}

/** Incremental fold for a growing event list (live view). */
export function useFold(events: RunEvent[]): RunState {
  const cache = useRef<{ n: number; state: RunState; first: RunEvent | undefined }>({ n: 0, state: initialState(), first: undefined });
  return useMemo(() => {
    const c = cache.current;
    if (events.length < c.n || events[0] !== c.first) {
      c.n = 0; c.state = initialState(); c.first = events[0];
    }
    let s = c.state;
    for (let i = c.n; i < events.length; i++) s = apply(s, events[i]);
    c.n = events.length; c.state = s;
    return s;
  }, [events]);
}

export interface Player {
  pos: number; playing: boolean; speed: number; clock: number; total: number; duration: number;
  setPlaying: (p: boolean) => void; setSpeed: (s: number) => void; seek: (pos: number) => void; step: (d: number) => void;
  state: RunState;
}

export const SPEEDS = [1, 5, 20, 60, 240];

/** Replays a recorded run on a clock (trace seconds x speed). Long idle gaps are skipped so nothing stalls. */
export function usePlayer(events: RunEvent[], opts: { startAtEnd: boolean; autoplay: boolean }): Player {
  const total = events.length;
  const duration = total ? events[total - 1].t : 0;
  const [pos, setPos] = useState(opts.startAtEnd ? total : 0);
  const [clock, setClock] = useState(opts.startAtEnd ? duration : 0);
  const [playing, setPlaying] = useState(opts.autoplay && !opts.startAtEnd);
  const [speed, setSpeed] = useState(20);

  useEffect(() => {
    setPos(opts.startAtEnd ? total : 0);
    setClock(opts.startAtEnd ? duration : 0);
    setPlaying(opts.autoplay && !opts.startAtEnd);
  }, [events]); // eslint-disable-line react-hooks/exhaustive-deps

  const posRef = useRef(pos); posRef.current = pos;
  const clockRef = useRef(clock); clockRef.current = clock;

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      let c = clockRef.current + dt * speed;
      let p = posRef.current;
      if (p < total && (events[p].t - c) / speed > 1.5) c = events[p].t; // skip idle gaps longer than 1.5 s on screen
      while (p < total && events[p].t <= c) p++;
      if (p >= total) {
        setPos(total); setClock(duration); setPlaying(false);
        return;
      }
      setPos(p); setClock(c);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, events, total, duration]);

  const seek = useCallback((p: number) => {
    const q = Math.max(0, Math.min(total, p));
    setPos(q);
    setClock(q === 0 ? 0 : events[q - 1].t);
  }, [events, total]);

  const step = useCallback((d: number) => {
    setPlaying(false);
    // step by narration lines: jump to the next/previous event that tells the story
    let p = posRef.current;
    if (d > 0) {
      p++;
      while (p < total && events[p - 1].type !== "narration" && events[p - 1].type !== "result") p++;
    } else {
      p--;
      while (p > 0 && events[p - 1].type !== "narration") p--;
    }
    seek(p);
  }, [events, total, seek]);

  const state = useFoldUpTo(events, pos);
  return { pos, playing, speed, clock, total, duration, setPlaying, setSpeed, seek, step, state };
}

function useFoldUpTo(events: RunEvent[], pos: number): RunState {
  const cache = useRef<{ events: RunEvent[] | null; n: number; state: RunState }>({ events: null, n: 0, state: initialState() });
  return useMemo(() => {
    const c = cache.current;
    if (c.events !== events || pos < c.n) {
      c.events = events;
      c.state = reduce(events, pos);
      c.n = pos;
      return c.state;
    }
    let s = c.state;
    for (let i = c.n; i < pos; i++) s = apply(s, events[i]);
    c.n = pos; c.state = s;
    return s;
  }, [events, pos]);
}

/** Ticks once a second while `on` (for live timers). */
export function useNow(on: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!on) return;
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, [on]);
  return now;
}

/** Simple polling fetch with loading/error state. */
export function usePoll<T>(fn: () => Promise<T>, everyMs: number, deps: unknown[]) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    const run = () => fn().then(
      (data) => alive && setState({ data, error: null, loading: false }),
      (e: unknown) => alive && setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e), loading: false })),
    );
    run();
    const id = everyMs > 0 ? window.setInterval(run, everyMs) : 0;
    return () => { alive = false; if (id) window.clearInterval(id); };
  }, [...deps, nonce]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, refresh: () => setNonce((n) => n + 1) };
}

/** Hash router: #/chat, #/run/<job>, #/bench, #/bench/<prefix>, #/bench/<prefix>/<job>. */
export function useRoute(): string[] {
  const parse = () => (window.location.hash.replace(/^#\/?/, "") || "chat").split("/").map(decodeURIComponent);
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function go(...parts: string[]) {
  window.location.hash = "/" + parts.map(encodeURIComponent).join("/");
}
