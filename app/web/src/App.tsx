import { useCallback, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { api } from "./lib/api";
import { go, useRoute } from "./lib/hooks";
import type { SetupOption } from "./lib/types";
import { Sidebar, type ThemePref } from "./components/Sidebar";
import { ChatThread } from "./components/Chat";
import { TaskPanel } from "./components/TaskPanel";
import { RunView, isTyping } from "./components/RunView";
import { BenchmarkView } from "./components/Benchmarks";
import { ErrorBox, Kbd } from "./components/ui";

const FALLBACK_SETUPS: SetupOption[] = [
  { id: "ocxv4", label: "v4", blurb: "GLM-5.3 Flash builds, checks run, Opus 5.5 judges and takes over on fix" },
];

function readPref<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const v = localStorage.getItem(key) as T | null;
    return v && allowed.includes(v) ? v : fallback;
  } catch { return fallback; }
}
function writePref(key: string, v: string) { try { localStorage.setItem(key, v); } catch { /* private mode */ } }

export default function App() {
  const route = useRoute();
  const [setups, setSetups] = useState<SetupOption[]>(FALLBACK_SETUPS);
  const [setupsErr, setSetupsErr] = useState<string | null>(null);
  const [setup, setSetupState] = useState(() => readPref("relay.setup", "ocxv4", ["ocxv4", "ocx28", "ocxopus", "ocxglm"] as const));
  const [theme, setThemeState] = useState<ThemePref>(() => readPref("relay.theme", "system", ["system", "light", "dark"] as const));
  const [present, setPresent] = useState(() => readPref("relay.present", "off", ["on", "off"] as const) === "on");
  const [help, setHelp] = useState(false);

  useEffect(() => {
    api.setups().then((r) => setSetups(r.setups)).catch((e) => setSetupsErr(e instanceof Error ? e.message : String(e)));
  }, []);
  const setSetup = useCallback((s: string) => { setSetupState(s as typeof setup); writePref("relay.setup", s); }, []);
  const setTheme = useCallback((t: ThemePref) => {
    setThemeState(t); writePref("relay.theme", t);
    if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  }, []);
  useEffect(() => { document.documentElement.classList.toggle("present", present); writePref("relay.present", present ? "on" : "off"); }, [present]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
          e.preventDefault();
          (document.querySelector(".composer-input") as HTMLTextAreaElement | null)?.focus() ?? go("chat");
        }
        return;
      }
      const k = e.key;
      if (k === "?") setHelp((h) => !h);
      else if (k === "Escape") setHelp(false);
      else if (k === "n" || k === "N") go("chat");
      else if (k === "b" || k === "B") go("bench");
      else if (k === "t" || k === "T") setTheme(({ system: "light", light: "dark", dark: "system" } as const)[theme]);
      else if (k === "p" || k === "P") setPresent((p) => !p);
      else if (k === "/") { e.preventDefault(); (document.querySelector(".composer-input, .search input") as HTMLElement | null)?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [theme, setTheme]);

  let main;
  if (route[0] === "run" && route[1]) {
    main = <RunView key={route[1]} job={route[1]} setups={setups} setup={setup} onSetup={setSetup} />;
  } else if (route[0] === "bench" && route[2]) {
    main = <RunView key={route[2]} job={route[2]} setups={setups} setup={setup} onSetup={setSetup} backTo={["bench", route[1]]} />;
  } else if (route[0] === "bench") {
    main = <BenchmarkView prefix={route[1] ?? null} />;
  } else {
    main = (
      <div className="workspace">
        <section className="center">
          {setupsErr ? <div className="center-pad"><ErrorBox title="The Relay server is not answering" message={setupsErr} /></div> : null}
          <ChatThread job={null} instruction="" setups={setups} setup={setup} onSetup={setSetup} />
        </section>
        <TaskPanel setup={setup} />
      </div>
    );
  }

  return (
    <div className="app">
      <a className="skip" href="#main">Skip to content</a>
      <Sidebar route={route} theme={theme} onTheme={setTheme} onShortcuts={() => setHelp(true)} present={present}
        onPresent={() => setPresent((p) => !p)} />
      <main id="main" className="main">{main}</main>
      {help ? <Shortcuts onClose={() => setHelp(false)} /> : null}
    </div>
  );
}

function Shortcuts({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  const rows: [string[], string][] = [
    [["N"], "New task"], [["B"], "Benchmarks"], [["/"], "Focus the task box or filter"], [["Cmd", "K"], "Focus the task box"],
    [["Enter"], "Send (then Enter again to start the run)"], [["Esc"], "Cancel or close"],
    [["Space"], "Play or pause a recording"], [["Left", "Right"], "Previous or next moment"], [["[", "]"], "Slower or faster replay"],
    [["Home", "End"], "Start or end of a recording"], [["T"], "Theme: system, light, dark"], [["P"], "Presentation size"], [["?"], "This list"],
  ];
  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <h2 className="h-section">Keyboard shortcuts</h2>
          <button ref={ref} className="btn ghost icon" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>
        <dl className="keys">
          {rows.map(([ks, what]) => (
            <div key={what} className="key-row"><dt>{ks.map((k) => <Kbd key={k}>{k}</Kbd>)}</dt><dd>{what}</dd></div>
          ))}
        </dl>
      </div>
    </div>
  );
}
