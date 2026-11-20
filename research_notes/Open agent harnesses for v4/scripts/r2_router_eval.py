"""A5 offline router, step 2: route outcomes, policies, leave-one-task-out CV. Reads units.json (r1_router_features.py).

Evaluation units: valid v2.8-family cascade runs (Opus judge + Opus takeover): takeover28/29/31/34/35.
Route outcome for each unit (solve, cost):
  cascade : observed (reward, run cost).
  glm     : judge shipped -> observed reward (no takeover ran), cost = builder (+spec) cost.
            judge said fix -> not observed. Three estimates of P(solve): OPT = dt1 GLM-alone reward on the task (n=1),
            POOL = mean over all GLM-only outcomes on the task (dt1 glm + any run whose judge shipped with no takeover/fix),
            PESS = 0 (trust the judge's "fix"). Cost = builder cost (observed).
  opus    : dt1 Opus-alone reward on the task (n=1), cost = GLM cost of first k commands + dt1 Opus-alone cost.
  stop    : 0 solves, cost = GLM cost of first k commands.
"""
import collections, itertools, json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
U = json.load(open(os.path.join(HERE, "units.json")))
CASC = ("takeover28", "takeover29", "takeover31", "takeover34", "takeover35")
ROUTES = ("glm", "cascade", "opus", "stop")
runs = json.load(open(next(p for p in (os.path.join(HERE, "runs.json"), os.path.join(HERE, "..", "runs.json")) if os.path.exists(p))))
opus = {r["task"]: (r["reward"], r["usd"]) for r in runs if r["prefix"] == "dt1" and r["setup"] == "opus" and r["reward"] is not None}
glm1 = {u["task"]: u["reward"] for u in U if u["setup"] == "glm"}
pool = collections.defaultdict(list)
for u in U:
    if u["setup"] == "glm" or (u["first_verdict"] == "ship" and not u["takeover_model"] and "fix" not in str(u["usd_role"])):
        pool[u["task"]].append(u["reward"])
units = [u for u in U if u["setup"] in CASC]
FEATS = list(units[0]["feats"]["3"].keys())


def outcomes(u, k, est):
    shipped = u["first_verdict"] == "ship" and not u["takeover_model"]
    if shipped:
        g = u["reward"]
    else:
        g = {"OPT": glm1.get(u["task"], 0.0), "POOL": float(np.mean(pool[u["task"]])) if pool[u["task"]] else 0.0, "PESS": 0.0}[est]
    ck = u["cost_at_k"][str(k)]
    o = opus[u["task"]]
    return {"cascade": (u["reward"], u["usd"]), "glm": (g, u["worker_usd"] + u["usd_role"].get("spec", 0)),
            "opus": (o[0], ck + o[1]), "stop": (0.0, ck)}


def X(us, k):
    return np.array([[u["feats"][str(k)][f] for f in FEATS] for u in us], float)


def util(o, lam):
    return o[0] - lam * o[1]


# ---- stump: one feature, one threshold, a route on each side (a == b allowed = constant policy)
def fit_stump(us, k, est, lam):
    O = [outcomes(u, k, est) for u in us]; Xm = X(us, k); best = None
    for a in ROUTES:  # constant policies first; ties keep the simpler rule
        s = sum(util(o[a], lam) for o in O)
        if best is None or s > best[0] + 1e-9:
            best = (s, None, None, a, a)
    for j, f in enumerate(FEATS):
        vals = np.unique(Xm[:, j])
        for th in (vals[:-1] + vals[1:]) / 2:
            lo = Xm[:, j] <= th
            for a, b in itertools.permutations(ROUTES, 2):
                s = sum(util(o[a] if l else o[b], lam) for o, l in zip(O, lo))
                if s > best[0] + 1e-9:
                    best = (s, f, th, a, b)
    return best


def apply_stump(m, u, k):
    _, f, th, a, b = m
    return a if f is None or u["feats"][str(k)][f] <= th else b


# ---- logistic regression (L2, numpy Newton) predicting P(solve) per route; cost per route = train mean
def lr_fit(Xm, y, w, l2=1.0):
    mu, sd = Xm.mean(0), Xm.std(0) + 1e-9; Z = np.c_[np.ones(len(Xm)), (Xm - mu) / sd]; b = np.zeros(Z.shape[1])
    for _ in range(50):
        p = 1 / (1 + np.exp(-Z @ b)); g = Z.T @ (w * (p - y)) + l2 * np.r_[0, b[1:]]
        H = Z.T @ (Z * (w * p * (1 - p))[:, None]) + l2 * np.diag(np.r_[1e-6, np.ones(len(b) - 1)])
        b -= np.linalg.solve(H, g)
    return mu, sd, b


def lr_pred(m, Xm):
    mu, sd, b = m; return 1 / (1 + np.exp(-np.c_[np.ones(len(Xm)), (Xm - mu) / sd] @ b))


def fit_lr(us, k, est, lam):
    O = [outcomes(u, k, est) for u in us]; Xm = X(us, k); models = {}
    for r in ("glm", "cascade", "opus"):
        y = np.array([o[r][0] for o in O])  # soft labels -> two weighted rows each
        X2 = np.r_[Xm, Xm]; y2 = np.r_[np.ones(len(y)), np.zeros(len(y))]; w2 = np.r_[y, 1 - y]
        models[r] = lr_fit(X2, y2, w2)
    cost = {r: float(np.mean([o[r][1] for o in O])) for r in ("glm", "cascade")}
    opus_extra = float(np.mean([o["opus"][1] - o["stop"][1] for o in O]))
    return models, cost, opus_extra


def apply_lr(m, u, k, lam):
    models, cost, opus_extra = m; x = X([u], k); ck = u["cost_at_k"][str(k)]
    s = {"glm": lr_pred(models["glm"], x)[0] - lam * cost["glm"], "cascade": lr_pred(models["cascade"], x)[0] - lam * cost["cascade"],
         "opus": lr_pred(models["opus"], x)[0] - lam * (ck + opus_extra), "stop": -lam * ck}
    return max(s, key=s.get)


def evaluate(policy, k, est):
    S = C = 0.0; picks = collections.Counter()
    for u in units:
        r = policy(u); o = outcomes(u, k, est)[r]; S += o[0]; C += o[1]; picks[r] += 1
    return S, C, picks


def loto(kind, k, est, lam):
    tasks = sorted({u["task"] for u in units}); choice = {}; rules = []
    for t in tasks:
        tr = [u for u in units if u["task"] != t]
        if kind == "stump":
            m = fit_stump(tr, k, est, lam); rules.append((t, m[1], None if m[2] is None else round(float(m[2]), 3), m[3], m[4]))
            for u in units:
                if u["task"] == t: choice[u["name"]] = apply_stump(m, u, k)
        else:
            m = fit_lr(tr, k, est, lam)
            for u in units:
                if u["task"] == t: choice[u["name"]] = apply_lr(m, u, k, lam)
    return evaluate(lambda u: choice[u["name"]], k, est), rules


def fmt(S, C, picks=None):
    return f"{S:5.2f} solves  ${C:6.2f}" + (f"  {dict(picks)}" if picks else "")


if __name__ == "__main__":
    print(f"units={len(units)} tasks={len({u['task'] for u in units})} "
          f"shipped={sum(1 for u in units if u['first_verdict']=='ship' and not u['takeover_model'])} "
          f"takeovers={sum(1 for u in units if u['takeover_model'])}")
    print("per-task GLM pool n:", {t: len(v) for t, v in sorted(pool.items()) if t in {u['task'] for u in units}})
    for est in ("OPT", "POOL", "PESS"):
        print(f"\n===== GLM estimate {est}")
        k0 = 5
        for r in ROUTES:
            print(f"  always {r:8s}", fmt(*evaluate(lambda u, r=r: r, k0, est)[:2]), "(k=5 for stop/opus prefix)")
        for k in (3, 5, 10):
            O = {u["name"]: outcomes(u, k, est) for u in units}
            # oracle A: most solves, then cheapest; oracle B: same solves as cascade per unit, cheapest
            orA = evaluate(lambda u: max(ROUTES, key=lambda r: (O[u["name"]][r][0], -O[u["name"]][r][1])), k, est)
            orB = evaluate(lambda u: min([r for r in ROUTES if O[u["name"]][r][0] >= O[u["name"]]["cascade"][0]],
                                         key=lambda r: O[u["name"]][r][1]), k, est)
            print(f" k={k:2d} oracle max-solves      ", fmt(*orA))
            print(f" k={k:2d} oracle >=cascade, cheap", fmt(*orB))
            for lam in (0.25, 0.5, 1.0):
                orL = evaluate(lambda u: max(ROUTES, key=lambda r: util(O[u["name"]][r], lam)), k, est)
                (s1, rules) = loto("stump", k, est, lam); (s2, _) = loto("lr", k, est, lam)
                feats = collections.Counter((r[1], r[3], r[4]) for r in rules)
                print(f" k={k:2d} lam={lam:4.2f} oracle-U ", fmt(*orL))
                print(f"            LOTO stump    ", fmt(*s1), " rules:", dict(feats))
                print(f"            LOTO logreg   ", fmt(*s2))
