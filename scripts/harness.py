"""One entry point for people (./harness from the repo root):

  ./harness run "fix the failing login test" ~/my-project --check "pytest -q"   your task, your folder
  ./harness try                                                             try it on a tiny bundled project
  ./harness doctor                                                          what is set up, what is missing
  ./harness tui                                                             the live view (same as Harness.command)
  ./harness status [prefix]                                                 how far an evaluation is
  ./harness report <prefix> [<prefix> ...]                                  results page in reports/

`run` copies the folder into a sandbox, works there, shows what changed and asks before applying it to your folder.
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def cmd_run(a) -> int:
    from phase2 import jobs as J
    from phase2 import local as L
    from phase2.setups import SETUPS

    folder = Path(a.folder).expanduser()
    if not folder.is_dir():
        print(f"There is no folder at {folder.resolve()}.\nGive the path of the project to work on, for example:\n"
              f'  ./harness run "fix the failing test" ~/Developer/some-project --check "pytest -q"\n'
              f"Or try it on the bundled example first: ./harness try")
        return 2
    if a.setup not in SETUPS:
        print(f"unknown setup {a.setup!r}; choose from: {', '.join(SETUPS)}")
        return 2
    policy, caps, *cfg = SETUPS[a.setup]
    config = cfg[0] if cfg else "configs/phase2-droid-glm.json"
    name = f"local-{a.setup}-{L.slug(a.instruction)}-{time.strftime('%m%d%H%M%S')}"
    task = L.make_task(Path(a.folder), a.instruction, ROOT / "jobs" / "_local_tasks" / name, check=a.check,
                       image=a.image)
    print(f"Sandbox task ready ({task.relative_to(ROOT)}). Starting {a.setup} on a copy of {Path(a.folder).resolve()}.")
    print("Your folder is not changed unless you say yes at the end. Ctrl-C stops watching; the run keeps going.\n")
    pid = J.start_run(ROOT, str(task), config, policy, caps, name)
    seen, t0 = 0, time.time()
    trial = None
    try:
        for trial, events in L.wait_for_trial(ROOT / "jobs" / name, J.pid_alive, pid):
            for line in L.status_lines(events, seen):
                print(f"[{(time.time() - t0) / 60:4.1f} min] {line}")
            seen = len(events)
    except KeyboardInterrupt:
        print(f"\nStopped watching. The run goes on: ./harness tui (job {name}).")
        return 0
    if not trial or not (trial / "result.json").exists():
        print(f"The run ended without a result; see jobs/{name}.log")
        return 1
    r = L.result(trial)
    print(f"\nDone in {r['minutes']:.0f} min, ${r['usd']:.2f} at API list price.")
    if r["error"]:
        print(f"The run hit an error: {r['error']} (jobs/{name}.log)")
    if r["report"]:
        print(f"Builder's report: {r['report'][:600]}")
    if a.check:
        print(f"Your check ({a.check}): {'PASSED' if r['reward'] == 1 else 'FAILED'}")
        if r["reward"] != 1 and r["check"]:
            print("  " + "\n  ".join(r["check"].splitlines()[-8:]))
    if not r["diff"].strip():
        print("No files changed.")
        return 0
    print(f"\nChanged:\n{r['stat']}")
    saved = ROOT / "jobs" / f"{name}.diff"
    saved.write_text(r["diff"])
    print(f"Full diff: {saved.relative_to(ROOT)}")
    yes = a.apply or (sys.stdin.isatty() and input(f"\nApply these changes to {Path(a.folder).resolve()}? [y/N] ")
                      .strip().lower() in ("y", "yes"))
    if yes:
        print(L.apply_diff(Path(a.folder).resolve(), r["diff"]))
    else:
        print(f"Not applied. Later: cd {Path(a.folder).resolve()} && git apply {saved}")
    return 0


def cmd_doctor(_a) -> int:
    import supervise

    def ok(name, good, fix=""):
        print(f"{'✓' if good else '✗'} {name}" + ("" if good else f"\n    fix: {fix}"))
        return good

    droid = shutil.which("droid") or Path("/Applications/Factory.app/Contents/Resources/bin/droid").exists()
    checks = [
        ok("Python environment (.venv with harbor)", (ROOT / ".venv/bin/harbor").exists(),
           "uv venv --python 3.12 .venv && uv pip install --python .venv -r requirements-phase2.txt"),
        ok("Docker running", supervise.docker_ok(), "colima start (or open Docker Desktop)"),
        ok("Internet reaches Factory", supervise.network_ok(), "check the network"),
        ok("Droid installed (Factory app)", bool(droid), "install the Factory app and sign in"),
    ]
    if checks[-1] and checks[-2]:
        for m in ("droid/glm-5.3-flash", "droid/claude-opus-5-5"):
            checks.append(ok(f"Model answers: {m}", supervise.model_ok(m), "open the Factory app and sign in; "
                                                                           "check the model is in its list"))
    ok("Terminal-Bench tasks downloaded (only for evaluations)",
       (Path.home() / "Developer/harbor-datasets/terminal-bench").is_dir(), "only needed for benchmark runs")
    print("\nReady." if all(checks) else "\nFix the ✗ items above, then run ./harness doctor again.")
    return 0 if all(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="harness", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="work on your own task in a copy of your folder")
    r.add_argument("instruction")
    r.add_argument("folder", nargs="?", default=".")
    r.add_argument("--check", default="", help="a command that should pass when the task is done, e.g. 'pytest -q'")
    r.add_argument("--setup", default="glmreview27", help="how to run it (default v2.7: GLM builds, Opus decides "
                                                          "how to work and reviews); 'opus' = Opus alone")
    r.add_argument("--image", default="ubuntu:24.04", help="the sandbox's base image, e.g. python:3.12")
    r.add_argument("--apply", action="store_true", help="apply the changes without asking")
    sub.add_parser("try", help="try it on a tiny bundled project with one bug (examples/average-bug)")
    sub.add_parser("doctor", help="check the setup")
    sub.add_parser("tui", help="open the live view")
    s = sub.add_parser("status", help="how far an evaluation is")
    s.add_argument("prefix", nargs="?")
    rp = sub.add_parser("report", help="write a results page")
    rp.add_argument("prefixes", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "try":
        import shutil
        import tempfile

        demo = Path(tempfile.mkdtemp(prefix="harness-try-")) / "average-bug"
        shutil.copytree(ROOT / "examples" / "average-bug", demo)
        subprocess.run(["git", "init", "-q"], cwd=demo)
        print(f"Trying it on a copy of examples/average-bug at {demo}\n")
        return cmd_run(argparse.Namespace(
            instruction="average() crashes on an empty list; make it return 0.0 as documented.", folder=str(demo),
            check="python -m unittest -q", setup="glmreview27", image="python:3.12-slim", apply=False))
    if a.cmd == "doctor":
        return cmd_doctor(a)
    if a.cmd == "tui":
        return subprocess.call([sys.executable, "-m", "phase2.tui"], cwd=ROOT)
    if a.cmd == "status":
        import progress
        progress.main([a.prefix] if a.prefix else [])
        return 0
    import report_html
    report_html.main(a.prefixes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
