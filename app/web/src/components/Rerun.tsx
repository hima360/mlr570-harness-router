import { useEffect, useRef, useState } from "react";
import { Repeat2 } from "lucide-react";
import { api } from "../lib/api";
import { go } from "../lib/hooks";
import type { SetupOption } from "../lib/types";
import { ErrorBox, Spinner } from "./ui";

let cachedSetups: SetupOption[] | null = null;

/** "Rerun this task": a fresh Harbor run of the same task with a chosen setup (job name app-<setup>-<task>-r<n>). */
export function RerunButton({ sourceJob, task }: { sourceJob: string; task: string }) {
  const [open, setOpen] = useState(false);
  const [setups, setSetups] = useState<SetupOption[]>(cachedSetups ?? []);
  const [setup, setSetup] = useState("ocxv4");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    if (!cachedSetups) api.setups().then((r) => { cachedSetups = r.setups; setSetups(r.setups); }).catch(() => undefined);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onClick = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("mousedown", onClick); };
  }, [open]);

  const start = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api.rerun(sourceJob, setup);
      setOpen(false);
      go("run", r.job);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  return (
    <div className="rerun" ref={box}>
      <button className="btn sm" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="dialog">
        <Repeat2 size={14} /> Rerun this task
      </button>
      {open ? (
        <div className="popover" role="dialog" aria-label="Rerun this task">
          <div className="pop-title">Rerun <span className="mono">{task}</span></div>
          <p className="muted small">Starts a fresh Harbor run with the benchmark's own tests. It uses real model quota and streams live.</p>
          <fieldset className="setup-radios">
            <legend className="sr-only">Setup</legend>
            {setups.map((s) => (
              <label key={s.id} className={`setup-radio${setup === s.id ? " on" : ""}`}>
                <input type="radio" name="rerun-setup" value={s.id} checked={setup === s.id} onChange={() => setSetup(s.id)} />
                <span><strong>{s.label}</strong><span className="muted small"> {s.blurb}</span></span>
              </label>
            ))}
          </fieldset>
          {err ? <ErrorBox title="Could not start the rerun" message={err} /> : null}
          <div className="pop-actions">
            <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn primary" onClick={start} disabled={busy || !setups.length} autoFocus>
              {busy ? <Spinner /> : null} Start rerun
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
